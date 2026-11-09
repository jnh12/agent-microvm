import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import io.github.jnh12.vmnest.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import software.amazon.awssdk.services.lambdamicrovms.model.RunMicrovmRequest;

class HostedTest {
  @TempDir Path temporary;

  @Test void existingSdkRegistryImagesAndMetricsUseCloudWithoutAws() throws Exception {
    var json = new ObjectMapper();
    var name = new AtomicReference<>("work");
    var state = new AtomicReference<>("running");
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext("/", exchange -> {
      assertEquals("Bearer test-token", exchange.getRequestHeaders().getFirst("Authorization"));
      String path = exchange.getRequestURI().getPath();
      var body = exchange.getRequestBody().readAllBytes();
      String previousState = state.get();
      if (path.equals("/v1/sandboxes") && exchange.getRequestMethod().equals("POST")) {
        var launch = json.readTree(body);
        assertEquals("default", launch.path("image").asText());
        assertEquals(3600, launch.path("duration").asInt());
        assertFalse(launch.has("idle_policy"));
        assertNotNull(exchange.getRequestHeaders().getFirst("Idempotency-Key"));
        name.set(launch.path("name").asText());
        state.set("running");
      }
      if (path.endsWith("/state")) state.set(json.readTree(body).path("state").asText());
      Object result = Map.of("id", "sandbox-one", "name", name.get(), "state",
          path.endsWith("/state") ? previousState : state.get(),
          "endpoint", "https://guest.example", "image", "default");
      if (path.equals("/v1/sandboxes") && exchange.getRequestMethod().equals("GET"))
        result = List.of(result);
      if (path.equals("/v1/images")) result = Map.of("items", List.of(
          Map.of("id", "default", "name", "default", "state", "ACTIVE")));
      if (path.equals("/v1/images/default")) result = Map.of("id", "default", "name", "default", "state", "ACTIVE");
      if (path.endsWith("/metrics")) result = Map.of("CPUUtilization", 4.5);
      if (path.endsWith("/logs")) result = Map.of("events", List.of(
          Map.of("message", "hello", "timestamp", 123L, "eventId", "event-one")));
      if (path.endsWith("/connection")) result = Map.of("token", "guest-token", "expires_in", 60);
      byte[] response = json.writeValueAsBytes(result);
      exchange.sendResponseHeaders(200, response.length);
      exchange.getResponseBody().write(response);
      exchange.close();
    });
    server.start();
    try (var cloud = new HostedClient(URI.create("http://127.0.0.1:" + server.getAddress().getPort()), "test-token")) {
      Path stateFile = temporary.resolve("state.json");
      Files.writeString(stateFile, "local state must not be read or overwritten", StandardCharsets.UTF_8);
      try (var registry = new SandboxRegistry(stateFile, cloud.controlPlane()); var sandbox = cloud.create("work")) {
        assertEquals("work", name.get());
        assertEquals("sandbox-one", registry.get("work").microvmId());
        assertEquals(1, registry.list().size());
        sandbox.suspend();
        assertEquals("suspended", sandbox.refresh());
        sandbox.resume();
        assertEquals("running", sandbox.state());
        assertEquals(1, cloud.images().list().size());
        assertEquals(1, cloud.images().managed().size());
        assertEquals("default", cloud.images().inspect("default").imageArn());
        assertEquals("default", cloud.images().waitActive("default", Duration.ofSeconds(3)).imageArn());
        assertTrue(cloud.images().prune(true).isEmpty());
        var metrics = cloud.observability().metrics("sandbox-one", Duration.ofMinutes(30));
        assertEquals(4.5, metrics.get("CPUUtilization").getAsDouble());
        assertTrue(metrics.get("MemoryUtilization").isEmpty());
        assertEquals("hello", cloud.observability().readLogs("/aws/lambda-microvms/sandbox-one", 100, null, null).getFirst().message());
        assertThrows(IllegalArgumentException.class, () -> Sandbox.create("unsafe", cloud.controlPlane(),
            RunMicrovmRequest.builder().executionRoleArn("admin").build(), 8080,
            Duration.ofSeconds(10), Duration.ofSeconds(10)));
      }
      assertEquals("terminated", state.get());
      assertEquals("local state must not be read or overwritten", Files.readString(stateFile));
      assertFalse(Files.exists(temporary.resolve("state.json.lock")));
    } finally { server.stop(0); }
  }

  @Test void validatesCloudCredentialsAndOrigin() {
    assertThrows(IllegalArgumentException.class, () -> new HostedClient(URI.create("http://remote.example"), "secret"));
    assertThrows(IllegalArgumentException.class, () -> new HostedClient(URI.create("https://user:pass@example.com"), "secret"));
    assertThrows(IllegalArgumentException.class, () -> new HostedClient(URI.create("https://example.com"), ""));
  }
}
