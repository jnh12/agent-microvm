package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.*;
import java.io.IOException;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import java.util.function.Supplier;

/** HTTP transport for the unchanged Python guest. No Python process on the caller. */
public final class AgentClient implements AutoCloseable {
  private final boolean ownsHttp;
  private final HttpClient http;
  private final URI endpoint;
  private final Supplier<String> tokens;
  private final int port;
  private final Duration timeout;
  private final ObjectMapper json = new ObjectMapper();
  private volatile String token;

  public AgentClient(URI endpoint, Supplier<String> tokens, int port, Duration timeout) {
    this(
        HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build(),
        endpoint,
        tokens,
        port,
        timeout,
        true);
  }

  /** Injected transport supports custom trust stores and local protocol tests. */
  public AgentClient(
      HttpClient http, URI endpoint, Supplier<String> tokens, int port, Duration timeout) {
    this(http, endpoint, tokens, port, timeout, false);
  }

  private AgentClient(
      HttpClient http,
      URI endpoint,
      Supplier<String> tokens,
      int port,
      Duration timeout,
      boolean ownsHttp) {
    this.ownsHttp = ownsHttp;
    if (!Set.of("http", "https").contains(endpoint.getScheme()) || endpoint.getHost() == null)
      throw new IllegalArgumentException("Absolute HTTP(S) endpoint required");
    if (port < 1 || port > 65535 || port == Ports.hook())
      throw new IllegalArgumentException("Invalid or reserved port");
    if (timeout.isNegative() || timeout.isZero())
      throw new IllegalArgumentException("Positive timeout required");
    this.http = http;
    this.endpoint = endpoint;
    this.tokens = tokens;
    this.port = port;
    this.timeout = timeout;
  }

  @Override
  public void close() {
    if (ownsHttp) http.close();
  }

  public boolean health() throws IOException, InterruptedException {
    return request("/healthz", null).statusCode() == 200;
  }

  private HttpResponse<String> request(String path, Object body)
      throws IOException, InterruptedException {
    String payload = body == null ? null : json.writeValueAsString(body);
    for (int attempt = 0; ; attempt++) {
      if (token == null)
        token = Objects.requireNonNull(tokens.get(), "Missing authentication token");
      var builder =
          HttpRequest.newBuilder(endpoint.resolve(path))
              .timeout(timeout)
              .header("X-aws-proxy-auth", token)
              .header("X-aws-proxy-port", Integer.toString(port));
      if (payload != null)
        builder
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(payload));
      var response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() != 401 || attempt == 1) return response;
      token = null;
    }
  }

  private JsonNode post(String path, Object body) throws IOException, InterruptedException {
    var response = request(path, body);
    if (response.statusCode() != 200)
      throw new IOException("Guest request failed: HTTP " + response.statusCode());
    return json.readTree(response.body());
  }

  private byte[] decode(JsonNode node, String field) throws IOException {
    String value = node.path(field).asText("");
    if (!node.path("encoding").asText("base64").equals("base64"))
      return value.getBytes(java.nio.charset.StandardCharsets.UTF_8);
    try {
      return Base64.getDecoder().decode(value);
    } catch (IllegalArgumentException e) {
      throw new IOException("Invalid guest base64", e);
    }
  }

  public ExecResult exec(
      String command,
      List<String> args,
      String cwd,
      Map<String, String> env,
      Duration processTimeout)
      throws IOException, InterruptedException {
    var body = new HashMap<String, Object>();
    body.put("command", command);
    body.put("args", args == null ? List.of() : args);
    if (cwd != null) body.put("cwd", cwd);
    if (env != null) body.put("env", env);
    if (processTimeout != null) body.put("timeout", processTimeout.toMillis() / 1000.0);
    var result = post("/v1/exec", body);
    return new ExecResult(
        result.path("exit_code").asInt(-1), decode(result, "stdout"), decode(result, "stderr"));
  }

  public byte[] readFile(String path) throws IOException, InterruptedException {
    return decode(post("/v1/fs/read", Map.of("path", path)), "content");
  }

  public void writeFile(String path, byte[] content) throws IOException, InterruptedException {
    post(
        "/v1/fs/write",
        Map.of(
            "path",
            path,
            "content",
            Base64.getEncoder().encodeToString(content),
            "encoding",
            "base64"));
  }
}
