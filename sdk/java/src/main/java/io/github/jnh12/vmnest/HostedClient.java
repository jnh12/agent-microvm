package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.TimeoutException;
import software.amazon.awssdk.services.lambdamicrovms.LambdaMicrovmsClient;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

/** VMNest Cloud transport. Credentials authorize your account, never an AWS account. */
public final class HostedClient implements AutoCloseable {
  private final URI url;
  private final String token;
  private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(15)).build();
  private final ObjectMapper json = new ObjectMapper();
  private final ControlPlane control = new ControlPlane(new Bridge(), this);

  public HostedClient(URI url, String token) {
    if (url.getHost() == null || (!"https".equals(url.getScheme())
            && !("http".equals(url.getScheme())
                && Set.of("localhost", "127.0.0.1", "::1").contains(url.getHost())))
        || url.getUserInfo() != null
        || url.getQuery() != null || url.getFragment() != null)
      throw new IllegalArgumentException("HTTPS API URL required (HTTP only on localhost)");
    if (token == null || token.isBlank()) throw new IllegalArgumentException("VMNest token required");
    this.url = URI.create(url.toString().replaceAll("/+$", ""));
    this.token = token;
  }

  public static HostedClient fromEnvironment() {
    return new HostedClient(
        URI.create(System.getenv().getOrDefault("VMNEST_API_URL", "https://vmnest.cc")),
        System.getenv("VMNEST_API_TOKEN"));
  }

  public ControlPlane controlPlane() { return control; }

  public JsonNode account() { return request("GET", "/v1/me", null, null); }

  public Images images() { return new Images(control.aws()); }

  public Observability observability() {
    var metrics = new software.amazon.awssdk.services.cloudwatch.CloudWatchClient() {
      @Override public String serviceName() { return "vmnest-cloud"; }
      @Override public void close() {}
      @Override public software.amazon.awssdk.services.cloudwatch.model.GetMetricStatisticsResponse
          getMetricStatistics(software.amazon.awssdk.services.cloudwatch.model.GetMetricStatisticsRequest req) {
        String id = req.dimensions().stream().filter(d -> "MicrovmId".equals(d.name()))
            .findFirst().orElseThrow().value();
        var data = request("GET", "/v1/sandboxes/" + part(id) + "/metrics?minutes=30", null, null);
        var result = software.amazon.awssdk.services.cloudwatch.model.GetMetricStatisticsResponse.builder();
        var value = data.path(req.metricName());
        if (value.isNumber()) result.datapoints(software.amazon.awssdk.services.cloudwatch.model.Datapoint
            .builder().average(value.asDouble()).timestamp(Instant.now()).build());
        return result.build();
      }
    };
    var logs = new software.amazon.awssdk.services.cloudwatchlogs.CloudWatchLogsClient() {
      @Override public String serviceName() { return "vmnest-cloud"; }
      @Override public void close() {}
      @Override public software.amazon.awssdk.services.cloudwatchlogs.model.FilterLogEventsResponse
          filterLogEvents(software.amazon.awssdk.services.cloudwatchlogs.model.FilterLogEventsRequest req) {
        String prefix = "/aws/lambda-microvms/";
        if (req.logGroupName() == null || !req.logGroupName().startsWith(prefix))
          throw new IllegalArgumentException("Hosted logs require a sandbox log group");
        String path = "/v1/sandboxes/" + part(req.logGroupName().substring(prefix.length()))
            + "/logs?limit=" + Objects.requireNonNullElse(req.limit(), 1000);
        if (req.startTime() != null) path += "&start_time=" + req.startTime();
        if (req.nextToken() != null) path += "&cursor=" + part(req.nextToken());
        var data = request("GET", path, null, null);
        var events = new ArrayList<software.amazon.awssdk.services.cloudwatchlogs.model.FilteredLogEvent>();
        for (var event : data.path("events")) events.add(
            software.amazon.awssdk.services.cloudwatchlogs.model.FilteredLogEvent.builder()
                .message(event.path("message").asText()).timestamp(event.path("timestamp").asLong())
                .eventId(event.path("eventId").asText()).build());
        return software.amazon.awssdk.services.cloudwatchlogs.model.FilterLogEventsResponse.builder()
            .events(events).nextToken(data.path("next_cursor").asText(null)).build();
      }
    };
    return new Observability(metrics, logs);
  }

  public Sandbox create(String name) throws InterruptedException, TimeoutException {
    return Sandbox.create(name, control, RunMicrovmRequest.builder().build(), Ports.agent(),
        Duration.ofMinutes(3), Duration.ofMinutes(5));
  }

  public List<JsonNode> sandboxes() {
    var result = new ArrayList<JsonNode>();
    int offset = 0;
    while (true) {
      var page = request("GET", "/v1/sandboxes?limit=100&offset=" + offset, null, null);
      var items = page.isArray() ? page : page.path("items");
      items.forEach(result::add);
      if (items.size() < 100) return List.copyOf(result);
      offset += items.size();
    }
  }

  JsonNode request(String method, String path, Object body, String idempotency) {
    try {
      var builder = HttpRequest.newBuilder(URI.create(url + path))
          .timeout(Duration.ofMinutes(3)).header("Authorization", "Bearer " + token);
      if (idempotency != null) builder.header("Idempotency-Key", idempotency);
      if (body != null) builder.header("Content-Type", "application/json");
      builder.method(method, body == null ? HttpRequest.BodyPublishers.noBody()
          : HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body)));
      var response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() == 404)
        throw ResourceNotFoundException.builder().message("VMNest resource not found").build();
      if (response.statusCode() < 200 || response.statusCode() >= 300)
        throw new IllegalStateException("VMNest request failed (" + response.statusCode() + ")");
      return response.body().isBlank() ? json.createObjectNode() : json.readTree(response.body());
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("VMNest request interrupted", e);
    } catch (IOException e) {
      throw new UncheckedIOException("VMNest request failed", e);
    }
  }

  static String part(String value) {
    return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
  }

  RunMicrovmResponse launch(String name, RunMicrovmRequest request) {
    if (request.executionRoleArn() != null || !request.egressNetworkConnectors().isEmpty()
        || !request.ingressNetworkConnectors().isEmpty())
      throw new IllegalArgumentException("AWS roles and connectors require self-hosted mode");
    if (request.imageVersion() != null)
      throw new IllegalArgumentException("Hosted images are immutable; select the image ID");
    var body = new LinkedHashMap<String, Object>();
    body.put("name", name);
    body.put("image", Objects.requireNonNullElse(request.imageIdentifier(), "default"));
    body.put("duration", Objects.requireNonNullElse(request.maximumDurationInSeconds(), 3600));
    if (request.idlePolicy() != null) {
      var idle = request.idlePolicy();
      body.put("idle_policy", Map.of("max_idle_duration_seconds", idle.maxIdleDurationSeconds(),
          "suspended_duration_seconds", idle.suspendedDurationSeconds(),
          "auto_resume_enabled", idle.autoResumeEnabled()));
    }
    var result = request("POST", "/v1/sandboxes", body,
        Objects.requireNonNullElseGet(request.clientToken(), () -> UUID.randomUUID().toString()));
    return RunMicrovmResponse.builder().microvmId(result.path("id").asText()).build();
  }

  private JsonNode state(String id, String state) {
    var result = request("POST", "/v1/sandboxes/" + part(id) + "/state", Map.of("state", state), null);
    if ("running".equals(state)) return result;
    long deadline = System.nanoTime() + Duration.ofMinutes(2).toNanos();
    while (!state.equals(result.path("state").asText())) {
      String observed = result.path("state").asText();
      if (Set.of("terminated", "failed").contains(observed)) {
        if ("terminated".equals(state)) return result;
        throw new IllegalStateException("Sandbox entered " + observed);
      }
      if (System.nanoTime() >= deadline)
        throw new IllegalStateException("Sandbox did not become " + state + "; inspect it before retrying");
      try { Thread.sleep(1000); }
      catch (InterruptedException e) {
        Thread.currentThread().interrupt(); throw new IllegalStateException("Sandbox wait interrupted", e);
      }
      result = request("GET", "/v1/sandboxes/" + part(id), null, null);
    }
    return result;
  }

  public JsonNode image(String id) { return request("GET", "/v1/images/" + part(id), null, null); }

  public JsonNode buildImage(String name, Path directory) throws IOException, InterruptedException {
    if (!Images.isGuestDirectory(directory)) throw new IOException("Guest requires a Dockerfile");
    Path archive = Files.createTempFile("vmnest-image-", ".zip");
    try {
      Images.archive(directory, archive);
      var upload = request("POST", "/v1/images/uploads",
          Map.of("name", name, "size_bytes", Files.size(archive)), null);
      URI destination = URI.create(upload.path("url").asText());
      if (!"https".equals(destination.getScheme())) throw new IOException("HTTPS upload required");
      String boundary = "vmnest-" + UUID.randomUUID();
      var parts = new ArrayList<HttpRequest.BodyPublisher>();
      var fields = upload.path("fields").fields();
      while (fields.hasNext()) {
        var field = fields.next();
        if (!field.getKey().matches("[A-Za-z0-9_-]+")) throw new IOException("Invalid upload field");
        parts.add(HttpRequest.BodyPublishers.ofString("--" + boundary
            + "\r\nContent-Disposition: form-data; name=\"" + field.getKey() + "\"\r\n\r\n"
            + field.getValue().asText() + "\r\n"));
      }
      parts.add(HttpRequest.BodyPublishers.ofString("--" + boundary
          + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"guest.zip\""
          + "\r\nContent-Type: application/zip\r\n\r\n"));
      parts.add(HttpRequest.BodyPublishers.ofFile(archive));
      parts.add(HttpRequest.BodyPublishers.ofString("\r\n--" + boundary + "--\r\n"));
      var response = http.send(HttpRequest.newBuilder(destination).timeout(Duration.ofMinutes(3))
          .header("Content-Type", "multipart/form-data; boundary=" + boundary)
          .POST(HttpRequest.BodyPublishers.concat(parts.toArray(HttpRequest.BodyPublisher[]::new)))
          .build(), HttpResponse.BodyHandlers.discarding());
      if (response.statusCode() < 200 || response.statusCode() >= 300)
        throw new IOException("Guest image upload failed (" + response.statusCode() + ")");
      return request("POST", "/v1/images", Map.of("name", name,
          "upload_id", upload.path("upload_id").asText()), UUID.randomUUID().toString());
    } finally { Files.deleteIfExists(archive); }
  }

  public JsonNode waitForImage(String id, Duration timeout) throws InterruptedException, TimeoutException {
    long deadline = System.nanoTime() + timeout.toNanos();
    while (System.nanoTime() < deadline) {
      var result = image(id);
      String state = result.path("state").asText().toUpperCase(Locale.ROOT);
      if ("ACTIVE".equals(state)) return result;
      if (Set.of("FAILED", "DELETED").contains(state)) throw new IllegalStateException("Image " + state);
      Thread.sleep(5000);
    }
    throw new TimeoutException("Image did not become active");
  }

  @Override public void close() { http.shutdownNow(); }

  private final class Bridge implements LambdaMicrovmsClient {
    @Override public String serviceName() { return "vmnest-cloud"; }
    @Override public void close() { HostedClient.this.close(); }
    @Override public RunMicrovmResponse runMicrovm(RunMicrovmRequest request) {
      return launch("vmnest-" + UUID.randomUUID().toString().substring(0, 12), request);
    }
    @Override public GetMicrovmResponse getMicrovm(GetMicrovmRequest request) {
      var item = HostedClient.this.request("GET", "/v1/sandboxes/" + part(request.microvmIdentifier()), null, null);
      return GetMicrovmResponse.builder().microvmId(item.path("id").asText())
          .state(item.path("state").asText().toUpperCase(Locale.ROOT))
          .endpoint(item.path("endpoint").asText(null)).imageArn(item.path("image").asText("default")).build();
    }
    @Override public ListMicrovmsResponse listMicrovms(ListMicrovmsRequest request) {
      var items = new ArrayList<MicrovmItem>();
      for (var item : sandboxes()) items.add(MicrovmItem.builder().microvmId(item.path("id").asText())
          .state(item.path("state").asText().toUpperCase(Locale.ROOT))
          .imageArn(item.path("image").asText("default")).build());
      return ListMicrovmsResponse.builder().items(items).build();
    }
    @Override public SuspendMicrovmResponse suspendMicrovm(SuspendMicrovmRequest request) {
      state(request.microvmIdentifier(), "suspended"); return SuspendMicrovmResponse.builder().build();
    }
    @Override public ResumeMicrovmResponse resumeMicrovm(ResumeMicrovmRequest request) {
      state(request.microvmIdentifier(), "running"); return ResumeMicrovmResponse.builder().build();
    }
    @Override public TerminateMicrovmResponse terminateMicrovm(TerminateMicrovmRequest request) {
      state(request.microvmIdentifier(), "terminated"); return TerminateMicrovmResponse.builder().build();
    }
    @Override public CreateMicrovmAuthTokenResponse createMicrovmAuthToken(CreateMicrovmAuthTokenRequest request) {
      if (request.allowedPorts().size() != 1 || request.allowedPorts().getFirst().port() == null)
        throw new IllegalArgumentException("One explicit connection port required");
      var result = HostedClient.this.request("POST", "/v1/sandboxes/" + part(request.microvmIdentifier())
          + "/connection?port=" + request.allowedPorts().getFirst().port(), null, null);
      return CreateMicrovmAuthTokenResponse.builder().authToken(Map.of("X-aws-proxy-auth", result.path("token").asText())).build();
    }
    @Override public ListMicrovmImagesResponse listMicrovmImages(ListMicrovmImagesRequest request) {
      var response = HostedClient.this.request("GET", "/v1/images", null, null);
      var result = new ArrayList<MicrovmImageSummary>();
      for (var item : response.isArray() ? response : response.path("items"))
        result.add(MicrovmImageSummary.builder().name(item.path("name").asText())
            .imageArn(item.path("id").asText()).state(item.path("state").asText())
            .latestActiveImageVersion("ACTIVE".equals(item.path("state").asText())
                ? item.path("imageVersion").asText(item.path("id").asText()) : null).build());
      return ListMicrovmImagesResponse.builder().items(result).build();
    }
    @Override public ListManagedMicrovmImagesResponse listManagedMicrovmImages(ListManagedMicrovmImagesRequest request) {
      var response = HostedClient.this.request("GET", "/v1/images?managed=true", null, null);
      var result = new ArrayList<ManagedMicrovmImageSummary>();
      for (var item : response.isArray() ? response : response.path("items"))
        result.add(ManagedMicrovmImageSummary.builder()
            .imageArn(item.path("id").asText()).build());
      return ListManagedMicrovmImagesResponse.builder().items(result).build();
    }
    @Override public GetMicrovmImageResponse getMicrovmImage(GetMicrovmImageRequest request) {
      var item = image(request.imageIdentifier());
      return GetMicrovmImageResponse.builder().name(item.path("name").asText())
          .imageArn(item.path("id").asText()).state(item.path("state").asText())
          .latestActiveImageVersion("ACTIVE".equals(item.path("state").asText())
              ? item.path("imageVersion").asText(item.path("id").asText()) : null)
          .latestFailedImageVersion("FAILED".equals(item.path("state").asText()) ? "failed" : null).build();
    }
    @Override public GetMicrovmImageVersionResponse getMicrovmImageVersion(GetMicrovmImageVersionRequest request) {
      var item = image(request.imageIdentifier());
      return GetMicrovmImageVersionResponse.builder()
          .stateReason(item.path("error").asText("Hosted image build failed")).build();
    }
    @Override public DeleteMicrovmImageResponse deleteMicrovmImage(DeleteMicrovmImageRequest request) {
      HostedClient.this.request("DELETE", "/v1/images/" + part(request.imageIdentifier()), null, null);
      return DeleteMicrovmImageResponse.builder().build();
    }
  }
}
