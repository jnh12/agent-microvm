import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.HttpServer;
import io.github.jnh12.vmnest.*;
import java.net.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class SdkTest {
  @Test
  void lifecycleAndPolling() throws Exception {
    var state = new java.util.concurrent.atomic.AtomicReference<String>("RUNNING");
    var terminated = new AtomicInteger();
    var aws =
        (software.amazon.awssdk.services.lambdamicrovms.LambdaMicrovmsClient)
            java.lang.reflect.Proxy.newProxyInstance(
                getClass().getClassLoader(),
                new Class[] {
                  software.amazon.awssdk.services.lambdamicrovms.LambdaMicrovmsClient.class
                },
                (proxy, method, args) -> {
                  return switch (method.getName()) {
                    case "getMicrovm" ->
                        software.amazon.awssdk.services.lambdamicrovms.model.GetMicrovmResponse
                            .builder()
                            .microvmId("vm-test")
                            .state(state.get())
                            .endpoint("test.invalid")
                            .build();
                    case "suspendMicrovm" -> {
                      state.set("SUSPENDED");
                      yield null;
                    }
                    case "resumeMicrovm" -> {
                      state.set("RUNNING");
                      yield null;
                    }
                    case "terminateMicrovm" -> {
                      terminated.incrementAndGet();
                      state.set("TERMINATED");
                      yield null;
                    }
                    default -> throw new UnsupportedOperationException(method.getName());
                  };
                });
    var control = new ControlPlane(aws);
    var sandbox = Sandbox.attach("test", "vm-test", control, 8081, Duration.ofSeconds(5));
    assertEquals("running", sandbox.state());
    sandbox.suspend();
    assertEquals("suspended", sandbox.refresh());
    sandbox.resume();
    assertEquals("running", sandbox.state());
    sandbox.stop();
    sandbox.close();
    assertEquals(1, terminated.get());
    assertThrows(
        IllegalStateException.class,
        () -> control.waitUntilRunning("vm-test", Duration.ofSeconds(1), Duration.ofMillis(1)));
    state.set("PENDING");
    assertThrows(
        java.util.concurrent.TimeoutException.class,
        () -> control.waitUntilRunning("vm-test", Duration.ofMillis(5), Duration.ofMillis(1)));
  }

  @Test
  void awsWireProtocol() throws Exception {
    Check.main(new String[0]);
  }

  @Test
  void guestProtocol() throws Exception {
    var attempts = new AtomicInteger();
    var tokens = new AtomicInteger();
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        x -> {
          int status = 200;
          String body;
          if (attempts.incrementAndGet() == 1) {
            status = 401;
            body = "{}";
          } else {
            assertEquals("token2", x.getRequestHeaders().getFirst("X-aws-proxy-auth"));
            assertEquals("8081", x.getRequestHeaders().getFirst("X-aws-proxy-port"));
            body =
                switch (x.getRequestURI().getPath()) {
                  case "/v1/exec" ->
                      "{\"exit_code\":7,\"stdout\":\"AP8=\",\"stderr\":\"ZXJy\",\"encoding\":\"base64\"}";
                  case "/v1/fs/read" -> "{\"content\":\"AP8=\",\"encoding\":\"base64\"}";
                  default -> "{}";
                };
          }
          byte[] bytes = body.getBytes();
          x.sendResponseHeaders(status, bytes.length);
          x.getResponseBody().write(bytes);
          x.close();
        });
    server.start();
    try {
      var agent =
          new AgentClient(
              URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
              () -> "token" + tokens.incrementAndGet(),
              8081,
              Duration.ofSeconds(3));
      assertTrue(agent.health());
      assertEquals(2, tokens.get());
      var result =
          agent.exec(
              "python",
              List.of("-c", "print(1)"),
              "/work",
              Map.of("X", "1"),
              Duration.ofSeconds(2));
      assertEquals(7, result.exitCode());
      assertFalse(result.success());
      assertArrayEquals(new byte[] {0, -1}, result.stdout());
      assertArrayEquals(new byte[] {0, -1}, agent.readFile("/binary"));
      agent.writeFile("/binary", new byte[] {0, -1});
    } finally {
      server.stop(0);
    }
  }

  @Test
  void validatesPortAndCopiesBytes() {
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new AgentClient(
                URI.create("https://example.com"), () -> "x", 9000, Duration.ofSeconds(1)));
    byte[] bytes = {1};
    var result = new ExecResult(0, bytes, bytes);
    bytes[0] = 2;
    assertEquals(1, result.stdout()[0]);
  }
}
