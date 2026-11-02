import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.*;
import io.github.jnh12.vmnest.*;
import java.net.*;
import java.net.http.*;
import java.nio.file.*;
import java.security.*;
import java.time.Duration;
import javax.net.ssl.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class TlsTest {
  @TempDir Path dir;

  @Test
  void optOutIsPerClientAndForwardWhileDefaultRejectsUntrustedCertificate() throws Exception {
    String executable =
        System.getProperty("os.name").startsWith("Windows") ? "keytool.exe" : "keytool";
    Path store = dir.resolve("server.p12");
    var process =
        new ProcessBuilder(
                Path.of(System.getProperty("java.home"), "bin", executable).toString(),
                "-genkeypair",
                "-alias",
                "test",
                "-keyalg",
                "RSA",
                "-keystore",
                store.toString(),
                "-storepass",
                "test-password",
                "-keypass",
                "test-password",
                "-dname",
                "CN=wrong-host.invalid",
                "-validity",
                "1",
                "-noprompt")
            .redirectErrorStream(true)
            .redirectOutput(dir.resolve("keytool.log").toFile())
            .start();
    assertTrue(process.waitFor(30, java.util.concurrent.TimeUnit.SECONDS));
    assertEquals(0, process.exitValue());
    var keys = KeyStore.getInstance("PKCS12");
    try (var input = Files.newInputStream(store)) {
      keys.load(input, "test-password".toCharArray());
    }
    var managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
    managers.init(keys, "test-password".toCharArray());
    var context = SSLContext.getInstance("TLS");
    context.init(managers.getKeyManagers(), null, null);
    var server = HttpsServer.create(new InetSocketAddress("localhost", 0), 0);
    server.setHttpsConfigurator(new HttpsConfigurator(context));
    server.createContext(
        "/",
        exchange -> {
          var bytes = "ok".getBytes();
          exchange.sendResponseHeaders(200, bytes.length);
          exchange.getResponseBody().write(bytes);
          exchange.close();
        });
    server.start();
    var endpoint = URI.create("https://localhost:" + server.getAddress().getPort());
    try {
      try (var secure = new AgentClient(endpoint, () -> "token", 8080, Duration.ofSeconds(5))) {
        assertFalse(secure.health());
      }
      try (var insecure =
          new AgentClient(endpoint, () -> "token", 8080, Duration.ofSeconds(5), false)) {
        assertTrue(insecure.health());
      }
      try (var forward = new PortForward(endpoint, 8080, 0, () -> "token", false);
          var http = HttpClient.newHttpClient()) {
        assertEquals(
            200,
            http.send(
                    HttpRequest.newBuilder(URI.create("http://localhost:" + forward.localPort()))
                        .build(),
                    HttpResponse.BodyHandlers.ofString())
                .statusCode());
      }
      try (var secure = new AgentClient(endpoint, () -> "token", 8080, Duration.ofSeconds(5))) {
        assertFalse(secure.health());
      }
    } finally {
      server.stop(0);
    }
  }
}
