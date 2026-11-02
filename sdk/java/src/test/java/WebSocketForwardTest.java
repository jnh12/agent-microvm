import static org.junit.jupiter.api.Assertions.*;

import io.github.jnh12.vmnest.PortForward;
import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

@Timeout(15)
class WebSocketForwardTest {
  static String head(InputStream input) throws IOException {
    var out = new ByteArrayOutputStream();
    int tail = 0;
    while (out.size() < 65536) {
      int b = input.read();
      if (b < 0) throw new EOFException();
      out.write(b);
      tail = (tail << 8) | b;
      if (tail == 0x0d0a0d0a) return out.toString(StandardCharsets.ISO_8859_1);
    }
    throw new IOException("Headers too large");
  }

  static void write(Socket socket, String value) throws IOException {
    socket.getOutputStream().write(value.getBytes(StandardCharsets.ISO_8859_1));
  }

  static String request() {
    return "GET /chat?x=1 HTTP/1.1\r\n"
               + "Host: localhost\r\n"
               + "Connection: keep-alive, Upgrade\r\n"
               + "Upgrade: websocket\r\n"
               + "Sec-WebSocket-Version: 13\r\n"
               + "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
               + "Sec-WebSocket-Protocol: vite-hmr, lambda-microvms.authentication.evil,"
               + " lambda-microvms.port.1\r\n"
               + "X-aws-proxy-auth: evil\r\n"
               + "X-aws-proxy-port: 1\r\n\r\n";
  }

  static String switching(String protocol) {
    return "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        + "Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\nSec-WebSocket-Protocol: "
        + protocol
        + "\r\n\r\n";
  }

  static Socket client(PortForward forward) throws IOException {
    Socket client = new Socket(InetAddress.getLoopbackAddress(), forward.localPort());
    client.setSoTimeout(5000);
    return client;
  }

  static PortForward forward(ServerSocket upstream, java.util.function.Supplier<String> token)
      throws IOException {
    return new PortForward(
        URI.create("http://127.0.0.1:" + upstream.getLocalPort()), 8000, 0, token);
  }

  static ServerSocket server() throws IOException {
    return new ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"));
  }

  @Test
  void preservesHandshakeAndFramesInBothDirections() throws Exception {
    // Masked fragmented text, continuation, binary, ping, pong, and close frames.
    byte[][] frames = {
      {1, (byte) 129, 1, 2, 3, 4, (byte) ('h' ^ 1)},
      {(byte) 128, (byte) 129, 1, 2, 3, 4, (byte) ('i' ^ 1)},
      {(byte) 130, (byte) 130, 1, 2, 3, 4, 1, (byte) 253},
      {(byte) 137, (byte) 128, 1, 2, 3, 4},
      {(byte) 138, (byte) 128, 1, 2, 3, 4},
      {(byte) 136, (byte) 128, 1, 2, 3, 4}
    };
    byte[] greeting = {(byte) 129, 2, 'o', 'k'};
    try (var upstream = server();
        var executor = Executors.newVirtualThreadPerTaskExecutor();
        var proxy = forward(upstream, () -> "secret")) {
      var peer =
          executor.submit(
              () -> {
                try (Socket socket = upstream.accept()) {
                  socket.setSoTimeout(5000);
                  String request = head(socket.getInputStream());
                  assertTrue(request.startsWith("GET /chat?x=1 HTTP/1.1\r\n"));
                  assertTrue(request.contains("Host: 127.0.0.1:" + upstream.getLocalPort()));
                  assertTrue(
                      request.contains(
                          "vite-hmr, lambda-microvms, lambda-microvms.authentication.secret,"
                              + " lambda-microvms.port.8000"));
                  assertFalse(request.contains("evil"));
                  assertFalse(request.toLowerCase().contains("x-aws-proxy-auth"));
                  var response = new ByteArrayOutputStream();
                  response.write(switching("vite-hmr").getBytes(StandardCharsets.ISO_8859_1));
                  response.write(greeting); // First frame shares a write with the handshake.
                  socket.getOutputStream().write(response.toByteArray());
                  for (byte[] frame : frames)
                    assertArrayEquals(frame, socket.getInputStream().readNBytes(frame.length));
                  socket.getOutputStream().write(new byte[] {(byte) 136, 0});
                }
                return null;
              });
      try (var client = client(proxy)) {
        write(client, request());
        String response = head(client.getInputStream());
        assertTrue(response.contains("sec-websocket-protocol: vite-hmr"));
        assertFalse(response.contains("secret"));
        assertArrayEquals(greeting, client.getInputStream().readNBytes(greeting.length));
        for (byte[] frame : frames) client.getOutputStream().write(frame);
        assertArrayEquals(new byte[] {(byte) 136, 0}, client.getInputStream().readNBytes(2));
        assertEquals(-1, client.getInputStream().read());
      }
      peer.get(5, TimeUnit.SECONDS);
    }
  }

  @Test
  void refreshesUnauthorizedOnceAndDoesNotLeakRejectedBody() throws Exception {
    var calls = new AtomicInteger();
    try (var upstream = server();
        var executor = Executors.newVirtualThreadPerTaskExecutor();
        var proxy = forward(upstream, () -> "token" + calls.incrementAndGet())) {
      var peer =
          executor.submit(
              () -> {
                for (int i = 1; i <= 2; i++)
                  try (Socket socket = upstream.accept()) {
                    socket.setSoTimeout(5000);
                    assertTrue(
                        head(socket.getInputStream())
                            .contains("lambda-microvms.authentication.token" + i));
                    write(socket, "HTTP/1.1 401 Unauthorized\r\nContent-Length: 6\r\n\r\nsecret");
                  }
                return null;
              });
      try (var client = client(proxy)) {
        write(client, request());
        String response =
            new String(client.getInputStream().readAllBytes(), StandardCharsets.ISO_8859_1);
        assertTrue(response.startsWith("HTTP/1.1 401"));
        assertFalse(response.contains("secret"));
      }
      peer.get(5, TimeUnit.SECONDS);
      assertEquals(2, calls.get());
    }
  }

  @Test
  void closeDisconnectsActiveWebSocketsAndHidesRoutingProtocol() throws Exception {
    var connected = new CountDownLatch(1);
    try (var upstream = server();
        var executor = Executors.newVirtualThreadPerTaskExecutor();
        var proxy = forward(upstream, () -> "secret")) {
      var peer =
          executor.submit(
              () -> {
                try (Socket socket = upstream.accept()) {
                  socket.setSoTimeout(5000);
                  head(socket.getInputStream());
                  write(socket, switching("lambda-microvms.authentication.secret"));
                  connected.countDown();
                  assertEquals(-1, socket.getInputStream().read());
                }
                return null;
              });
      try (var client = client(proxy)) {
        write(client, request());
        String response = head(client.getInputStream());
        assertFalse(response.contains("secret"));
        assertFalse(response.contains("sec-websocket-protocol"));
        assertTrue(connected.await(5, TimeUnit.SECONDS));
        proxy.close();
        assertEquals(-1, client.getInputStream().read());
      }
      peer.get(5, TimeUnit.SECONDS);
    }
  }

  @Test
  void ordinaryHttpHandlesPostAndClientHalfClose() throws Exception {
    var server = com.sun.net.httpserver.HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        exchange -> {
          byte[] body = exchange.getRequestBody().readAllBytes();
          exchange.sendResponseHeaders(200, body.length);
          exchange.getResponseBody().write(body);
          exchange.close();
        });
    server.start();
    try (var proxy =
            new PortForward(
                URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
                8000,
                0,
                () -> "secret");
        var client = client(proxy)) {
      write(
          client,
          "POST /echo HTTP/1.1\r\n"
              + "Host: localhost\r\n"
              + "Content-Length: 4\r\n"
              + "Connection: close\r\n\r\n"
              + "test");
      client.shutdownOutput();
      String response =
          new String(client.getInputStream().readAllBytes(), StandardCharsets.ISO_8859_1);
      assertTrue(response.startsWith("HTTP/1.1 200"));
      assertTrue(response.contains("test"));
    } finally {
      server.stop(0);
    }
  }

  @Test
  void worksWithRealJavaWebSocketClient() throws Exception {
    try (var upstream = server();
        var executor = Executors.newVirtualThreadPerTaskExecutor();
        var proxy = forward(upstream, () -> "secret");
        var http = HttpClient.newHttpClient()) {
      var peer =
          executor.submit(
              () -> {
                try (Socket socket = upstream.accept()) {
                  socket.setSoTimeout(5000);
                  String request = head(socket.getInputStream());
                  String key =
                      Arrays.stream(request.split("\r\n"))
                          .filter(line -> line.toLowerCase().startsWith("sec-websocket-key:"))
                          .findFirst()
                          .orElseThrow()
                          .split(":", 2)[1]
                          .trim();
                  String accept =
                      Base64.getEncoder()
                          .encodeToString(
                              MessageDigest.getInstance("SHA-1")
                                  .digest(
                                      (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
                                          .getBytes(StandardCharsets.US_ASCII)));
                  write(
                      socket,
                      "HTTP/1.1 101 Switching Protocols\r\n"
                          + "Upgrade: websocket\r\n"
                          + "Connection: Upgrade\r\n"
                          + "Sec-WebSocket-Accept: "
                          + accept
                          + "\r\nSec-WebSocket-Protocol: vite-hmr\r\n\r\n");
                  var input = socket.getInputStream();
                  assertEquals(129, input.read());
                  int length = input.read();
                  assertEquals(0x82, length);
                  byte[] mask = input.readNBytes(4), data = input.readNBytes(2);
                  assertEquals('h', data[0] ^ mask[0]);
                  assertEquals('i', data[1] ^ mask[1]);
                  socket.getOutputStream().write(new byte[] {(byte) 129, 2, 'o', 'k'});
                  input.read(); // Remain connected until client aborts.
                }
                return null;
              });
      var received = new CompletableFuture<String>();
      var ws =
          http.newWebSocketBuilder()
              .subprotocols("vite-hmr")
              .buildAsync(
                  URI.create("ws://localhost:" + proxy.localPort() + "/hmr"),
                  new WebSocket.Listener() {
                    public void onOpen(WebSocket ws) {
                      ws.request(1);
                    }

                    public CompletionStage<?> onText(
                        WebSocket ws, CharSequence data, boolean last) {
                      received.complete(data.toString());
                      return null;
                    }

                    public void onError(WebSocket ws, Throwable error) {
                      received.completeExceptionally(error);
                    }
                  })
              .get(5, TimeUnit.SECONDS);
      try {
        assertEquals("vite-hmr", ws.getSubprotocol());
        ws.sendText("hi", true).get(5, TimeUnit.SECONDS);
        assertEquals("ok", received.get(5, TimeUnit.SECONDS));
      } finally {
        ws.abort();
      }
      peer.get(5, TimeUnit.SECONDS);
    }
  }
}
