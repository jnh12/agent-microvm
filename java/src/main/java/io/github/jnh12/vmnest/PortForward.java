package io.github.jnh12.vmnest;

import com.sun.net.httpserver.HttpServer;
import java.io.*;
import java.net.*;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Supplier;

/** Loopback HTTP reverse proxy. WebSockets/upgrades are unsupported, as in the Python CLI. */
public final class PortForward implements AutoCloseable {
  private final HttpServer server;
  private final HttpClient http;
  private final ExecutorService workers = Executors.newVirtualThreadPerTaskExecutor();
  private static final Set<String> OMIT =
      Set.of(
          "host",
          "connection",
          "content-length",
          "transfer-encoding",
          "upgrade",
          "keep-alive",
          "proxy-authenticate",
          "proxy-authorization",
          "te",
          "trailer",
          "x-aws-proxy-auth",
          "x-aws-proxy-port");

  public PortForward(URI endpoint, int remotePort, int localPort, Supplier<String> token)
      throws IOException {
    if (remotePort < 1 || remotePort > 65535 || remotePort == Ports.hook())
      throw new IllegalArgumentException("Invalid or reserved port");
    if (!"https".equals(endpoint.getScheme())
        && !("http".equals(endpoint.getScheme())
            && Set.of("localhost", "127.0.0.1").contains(endpoint.getHost())))
      throw new IllegalArgumentException("HTTPS endpoint required");
    http =
        HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();
    server =
        HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), localPort), 0);
    server.setExecutor(workers);
    server.createContext(
        "/",
        exchange -> {
          try {
            String upgrade = exchange.getRequestHeaders().getFirst("Upgrade");
            if (upgrade != null && !upgrade.equalsIgnoreCase("h2c")) {
              exchange.sendResponseHeaders(501, -1);
              return;
            }
            Set<String> excluded = new HashSet<>(OMIT);
            excluded.add("http2-settings");
            for (String connection :
                exchange.getRequestHeaders().getOrDefault("Connection", List.of()))
              for (String header : connection.split(","))
                excluded.add(header.trim().toLowerCase(Locale.ROOT));
            byte[] body = exchange.getRequestBody().readNBytes(16 * 1024 * 1024 + 1);
            if (body.length > 16 * 1024 * 1024) {
              exchange.sendResponseHeaders(413, -1);
              return;
            }
            var target =
                URI.create(
                    endpoint.toString().replaceAll("/$", "")
                        + exchange.getRequestURI().toASCIIString());
            for (int attempt = 0; attempt < 2; attempt++) {
              var request =
                  HttpRequest.newBuilder(target)
                      .timeout(Duration.ofMinutes(5))
                      .method(
                          exchange.getRequestMethod(),
                          HttpRequest.BodyPublishers.ofByteArray(body));
              exchange
                  .getRequestHeaders()
                  .forEach(
                      (key, values) -> {
                        if (!excluded.contains(key.toLowerCase(Locale.ROOT)))
                          values.forEach(value -> request.header(key, value));
                      });
              request
                  .header("X-aws-proxy-auth", token.get())
                  .header("X-aws-proxy-port", Integer.toString(remotePort));
              var response = http.send(request.build(), HttpResponse.BodyHandlers.ofInputStream());
              try (var input = response.body()) {
                if (response.statusCode() == 401 && attempt == 0) continue;
                response
                    .headers()
                    .map()
                    .forEach(
                        (key, values) -> {
                          if (!OMIT.contains(key.toLowerCase(Locale.ROOT)))
                            exchange.getResponseHeaders().put(key, values);
                        });
                boolean noBody =
                    exchange.getRequestMethod().equals("HEAD")
                        || response.statusCode() == 204
                        || response.statusCode() == 304;
                exchange.sendResponseHeaders(response.statusCode(), noBody ? -1 : 0);
                if (!noBody) input.transferTo(exchange.getResponseBody());
                return;
              }
            }
          } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            exchange.sendResponseHeaders(502, -1);
          } catch (Exception e) {
            try {
              exchange.sendResponseHeaders(502, -1);
            } catch (IOException ignored) {
            }
          } finally {
            exchange.close();
          }
        });
    server.start();
  }

  public int localPort() {
    return server.getAddress().getPort();
  }

  @Override
  public void close() {
    server.stop(0);
    workers.shutdownNow();
    http.shutdownNow();
  }
}
