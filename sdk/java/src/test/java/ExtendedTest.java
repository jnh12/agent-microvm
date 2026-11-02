import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.HttpServer;
import io.github.jnh12.vmnest.*;
import java.net.*;
import java.net.http.*;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.atomic.*;
import java.util.function.Consumer;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import software.amazon.awssdk.services.cloudwatch.*;
import software.amazon.awssdk.services.cloudwatch.model.*;
import software.amazon.awssdk.services.cloudwatchlogs.*;
import software.amazon.awssdk.services.cloudwatchlogs.model.*;
import software.amazon.awssdk.services.cloudwatchlogs.paginators.*;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;
import software.amazon.awssdk.services.lambdamicrovms.paginators.*;

class ExtendedTest {
  @TempDir Path dir;

  @SuppressWarnings("unchecked")
  static <T> T fake(Class<T> type, java.lang.reflect.InvocationHandler handler) {
    return (T)
        java.lang.reflect.Proxy.newProxyInstance(
            type.getClassLoader(),
            new Class[] {type},
            (p, m, a) ->
                m.getName().equals("serviceClientConfiguration") ? null : handler.invoke(p, m, a));
  }

  @Test
  void metricsSelectNewestAndPreserveMissing() {
    var client =
        fake(
            CloudWatchClient.class,
            (p, m, a) -> {
              var b = GetMetricStatisticsRequest.builder();
              ((Consumer<GetMetricStatisticsRequest.Builder>) a[0]).accept(b);
              var request = b.build();
              assertEquals("AWS/LambdaMicroVMs", request.namespace());
              assertEquals("vm-test", request.dimensions().getFirst().value());
              if (request.metricName().equals("MemoryUtilization"))
                return GetMetricStatisticsResponse.builder().build();
              return GetMetricStatisticsResponse.builder()
                  .datapoints(
                      Datapoint.builder()
                          .timestamp(Instant.ofEpochSecond(20))
                          .average(12.0)
                          .build(),
                      Datapoint.builder()
                          .timestamp(Instant.ofEpochSecond(10))
                          .average(99.0)
                          .build())
                  .build();
            });
    var result = new Observability(client, null).metrics("vm-test", Duration.ofMinutes(5));
    assertEquals(12, result.get("CPUUtilization").orElseThrow());
    assertTrue(result.get("MemoryUtilization").isEmpty());
  }

  @Test
  void logsTraversePagesAndTailAfterFilter() {
    var calls = new AtomicInteger();
    var client =
        fake(
            CloudWatchLogsClient.class,
            (p, m, a) -> {
              if (m.getName().equals("filterLogEventsPaginator"))
                return new FilterLogEventsIterable(
                    (CloudWatchLogsClient) p, (FilterLogEventsRequest) a[0]);
              var req = (FilterLogEventsRequest) a[0];
              calls.incrementAndGet();
              return req.nextToken() == null
                  ? FilterLogEventsResponse.builder()
                      .events(FilteredLogEvent.builder().message("match first").build())
                      .nextToken("page2")
                      .build()
                  : FilterLogEventsResponse.builder()
                      .events(FilteredLogEvent.builder().message("match last").build())
                      .build();
            });
    var events = new Observability(null, client).readLogs("group", 1, Instant.EPOCH, "match");
    assertEquals(2, calls.get());
    assertEquals("match last", events.getFirst().message());
  }

  @Test
  void imagePruneKeepsUsedAcrossAllPages() {
    var deleted = new ArrayList<String>();
    var client =
        fake(
            LambdaMicrovmsClient.class,
            (p, m, a) ->
                switch (m.getName()) {
                  case "listMicrovmsPaginator" ->
                      new ListMicrovmsIterable(
                          (LambdaMicrovmsClient) p, (ListMicrovmsRequest) a[0]);
                  case "listMicrovms" ->
                      ListMicrovmsResponse.builder()
                          .items(MicrovmItem.builder().imageArn("used").state("RUNNING").build())
                          .build();
                  case "listMicrovmImagesPaginator" ->
                      new ListMicrovmImagesIterable(
                          (LambdaMicrovmsClient) p, (ListMicrovmImagesRequest) a[0]);
                  case "listMicrovmImages" ->
                      ListMicrovmImagesResponse.builder()
                          .items(
                              MicrovmImageSummary.builder().imageArn("used").build(),
                              MicrovmImageSummary.builder().imageArn("unused").build())
                          .build();
                  case "deleteMicrovmImage" -> {
                    var b = DeleteMicrovmImageRequest.builder();
                    ((Consumer<DeleteMicrovmImageRequest.Builder>) a[0]).accept(b);
                    deleted.add(b.build().imageIdentifier());
                    yield DeleteMicrovmImageResponse.builder().build();
                  }
                  default -> throw new AssertionError(m.getName());
                });
    var images = new Images(client);
    assertThrows(IllegalArgumentException.class, () -> images.prune(false));
    assertEquals(List.of("unused"), images.prune(true));
    assertEquals(List.of("unused"), deleted);
  }

  @Test
  void archiveRequiresDockerfileAndExcludesCaches() throws Exception {
    Path source = Files.createDirectory(dir.resolve("source")), zip = dir.resolve("guest.zip");
    assertThrows(java.io.IOException.class, () -> Images.archive(source, zip));
    Files.writeString(source.resolve("Dockerfile"), "FROM scratch");
    Files.createDirectory(source.resolve(".git"));
    Files.writeString(source.resolve(".git/secret"), "ignored");
    Images.archive(source, zip);
    try (var archive = new java.util.zip.ZipFile(zip.toFile())) {
      assertNotNull(archive.getEntry("Dockerfile"));
      assertNull(archive.getEntry(".git/secret"));
    }
    assertThrows(
        IllegalArgumentException.class, () -> Images.archive(source, source.resolve("bad.zip")));
  }

  @Test
  void hostPolicyRejectsTraversalBeforeNetwork() throws Exception {
    var agent =
        new AgentClient(
            URI.create("https://invalid.example"), () -> "unused", 8080, Duration.ofSeconds(1));
    var files = new SandboxFiles(agent, "/work", List.of(dir), Duration.ofSeconds(1));
    assertThrows(
        java.io.IOException.class, () -> files.copyFromHost(dir.resolve("../outside"), "dest"));
    assertThrows(
        java.io.IOException.class, () -> files.copyToHost("src", dir.resolve("../outside")));
  }

  @Test
  void proxyHandlesJavaH2cAndPreservesBytes() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    var auth = new AtomicReference<String>();
    server.createContext(
        "/",
        x -> {
          auth.set(x.getRequestHeaders().getFirst("X-aws-proxy-auth"));
          assertEquals("8765", x.getRequestHeaders().getFirst("X-aws-proxy-port"));
          byte[] data = {0, -1};
          x.sendResponseHeaders(200, data.length);
          x.getResponseBody().write(data);
          x.close();
        });
    server.start();
    try (var forward =
        new PortForward(
            URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
            8765,
            0,
            () -> "scoped")) {
      var result =
          HttpClient.newHttpClient()
              .send(
                  HttpRequest.newBuilder(
                          URI.create("http://localhost:" + forward.localPort() + "/a?b=1"))
                      .build(),
                  HttpResponse.BodyHandlers.ofByteArray());
      assertEquals(200, result.statusCode());
      assertArrayEquals(new byte[] {0, -1}, result.body());
      assertEquals("scoped", auth.get());
    } finally {
      server.stop(0);
    }
  }

  @Test
  void registryPersistsAndRejectsDuplicate() throws Exception {
    var client =
        fake(
            LambdaMicrovmsClient.class,
            (p, m, a) ->
                GetMicrovmResponse.builder()
                    .microvmId("vm")
                    .state("RUNNING")
                    .endpoint("example.invalid")
                    .build());
    var control = new ControlPlane(client);
    Path file = dir.resolve("registry.json");
    var registry = new SandboxRegistry(file, control);
    registry.attach("test", "vm", 8081);
    assertThrows(IllegalArgumentException.class, () -> registry.attach("test", "vm2", 8080));
    assertThrows(java.io.IOException.class, () -> new SandboxRegistry(file, control));
    registry.close();
    try (var reopened = new SandboxRegistry(file, control)) {
      assertEquals("vm", reopened.list().getFirst().microvmId());
    }
  }

  @Test
  void guestFailureDoesNotLeakBodyAndBadBase64Fails() throws Exception {
    var status = new AtomicInteger(500);
    var text = new AtomicReference<>("sensitive-body");
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        x -> {
          byte[] bytes = text.get().getBytes();
          x.sendResponseHeaders(status.get(), bytes.length);
          x.getResponseBody().write(bytes);
          x.close();
        });
    server.start();
    try {
      var agent =
          new AgentClient(
              URI.create("http://localhost:" + server.getAddress().getPort()),
              () -> "test",
              8080,
              Duration.ofSeconds(2));
      var error = assertThrows(java.io.IOException.class, () -> agent.readFile("/a"));
      assertFalse(error.toString().contains("sensitive-body"));
      status.set(200);
      text.set("{\"content\":\"!!!\",\"encoding\":\"base64\"}");
      assertThrows(java.io.IOException.class, () -> agent.readFile("/a"));
    } finally {
      server.stop(0);
    }
  }
}
