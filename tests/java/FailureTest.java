import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.HttpServer;
import io.github.jnh12.vmnest.*;
import java.net.*;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

class FailureTest {
  @Test
  void failedReadinessRetainsLaunchedId() {
    var aws =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) ->
                switch (m.getName()) {
                  case "runMicrovm" -> RunMicrovmResponse.builder().microvmId("recover-me").build();
                  case "getMicrovm" -> throw new IllegalStateException("injected lookup failure");
                  default ->
                      throw new AssertionError("Unexpected destructive operation: " + m.getName());
                });
    var error =
        assertThrows(
            LaunchException.class,
            () ->
                Sandbox.create(
                    "test",
                    new ControlPlane(aws),
                    RunMicrovmRequest.builder()
                        .imageIdentifier("image")
                        .executionRoleArn("role")
                        .build(),
                    8080,
                    Duration.ofSeconds(1),
                    Duration.ofSeconds(1)));
    assertEquals("recover-me", error.microvmId());
  }

  @Test
  void invalidConfigurationNeverLaunches() {
    var aws =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> {
              throw new AssertionError("AWS should not be called");
            });
    assertThrows(
        IllegalArgumentException.class,
        () ->
            Sandbox.create(
                "",
                new ControlPlane(aws),
                RunMicrovmRequest.builder().build(),
                8080,
                Duration.ofSeconds(1),
                Duration.ofSeconds(1)));
  }

  @Test
  void interruptedPollStopsImmediately() throws Exception {
    var aws =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> GetMicrovmResponse.builder().state("PENDING").build());
    Thread.currentThread().interrupt();
    try {
      assertThrows(
          InterruptedException.class,
          () ->
              new ControlPlane(aws)
                  .waitUntilRunning("id", Duration.ofMinutes(1), Duration.ofSeconds(1)));
    } finally {
      Thread.interrupted();
    }
  }

  @Test
  void repeatedUnauthorizedRetriesOnlyOnce() throws Exception {
    var count = new AtomicInteger();
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        x -> {
          count.incrementAndGet();
          x.sendResponseHeaders(401, -1);
          x.close();
        });
    server.start();
    try (var agent =
        new AgentClient(
            URI.create("http://localhost:" + server.getAddress().getPort()),
            () -> "token",
            8080,
            Duration.ofSeconds(1))) {
      assertFalse(agent.health());
      assertEquals(2, count.get());
    } finally {
      server.stop(0);
    }
  }
}
