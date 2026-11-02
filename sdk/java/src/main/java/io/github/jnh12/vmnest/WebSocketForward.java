package io.github.jnh12.vmnest;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Supplier;
import javax.net.ssl.*;

/** Routes ordinary HTTP to the HTTP handler and upgrades to a byte-preserving duplex relay. */
final class WebSocketForward implements AutoCloseable {
  private final ServerSocket listener;
  private final ExecutorService workers = Executors.newVirtualThreadPerTaskExecutor();
  private final Set<Socket> sockets = ConcurrentHashMap.newKeySet();
  private final URI endpoint;
  private final int remotePort;
  private final Supplier<String> token;
  private final InetSocketAddress httpAddress;
  private volatile boolean closed;
  private final boolean verifyTls;

  WebSocketForward(
      URI endpoint,
      int remotePort,
      int localPort,
      Supplier<String> token,
      InetSocketAddress httpAddress,
      boolean verifyTls)
      throws IOException {
    this.verifyTls = verifyTls;
    this.endpoint = endpoint;
    this.remotePort = remotePort;
    this.token = token;
    this.httpAddress = httpAddress;
    listener = new ServerSocket();
    try {
      listener.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), localPort));
      workers.submit(this::accept);
    } catch (IOException | RuntimeException e) {
      listener.close();
      workers.shutdownNow();
      throw e;
    }
  }

  int localPort() {
    return listener.getLocalPort();
  }

  private void track(Socket socket) throws IOException {
    sockets.add(socket);
    if (closed) {
      release(socket);
      throw new IOException("Forward closed");
    }
  }

  private void release(Socket socket) {
    try {
      socket.close();
    } catch (IOException ignored) {
    }
    sockets.remove(socket);
  }

  private void accept() {
    while (!closed) {
      try {
        Socket client = listener.accept();
        track(client);
        workers.submit(() -> handle(client));
      } catch (IOException | RejectedExecutionException e) {
        if (!closed) close();
      }
    }
  }

  // Read exactly through CRLF CRLF: buffered read-ahead can otherwise swallow the first frame.
  static String readHead(InputStream input) throws IOException {
    var bytes = new ByteArrayOutputStream();
    int tail = 0;
    while (bytes.size() < 65536) {
      int b = input.read();
      if (b < 0) throw new EOFException("Incomplete HTTP headers");
      bytes.write(b);
      tail = (tail << 8) | b;
      if (tail == 0x0d0a0d0a) return bytes.toString(StandardCharsets.ISO_8859_1);
    }
    throw new IOException("HTTP headers too large");
  }

  private static Map<String, List<String>> headers(String head) throws IOException {
    var result = new LinkedHashMap<String, List<String>>();
    var lines = head.split("\r\n");
    for (int i = 1; i < lines.length; i++) {
      int colon = lines[i].indexOf(':');
      if (colon <= 0) throw new IOException("Invalid HTTP header");
      String name = lines[i].substring(0, colon).toLowerCase(Locale.ROOT);
      if (!name.matches("[!#$%&'*+.^_`|~0-9a-z-]+")) throw new IOException("Invalid header name");
      result
          .computeIfAbsent(name, key -> new ArrayList<>())
          .add(lines[i].substring(colon + 1).trim());
    }
    return result;
  }

  private static List<String> tokens(Map<String, List<String>> headers, String name) {
    return headers.getOrDefault(name, List.of()).stream()
        .flatMap(value -> Arrays.stream(value.split(",")))
        .map(String::trim)
        .toList();
  }

  private Socket connect(InetSocketAddress address, boolean tls) throws IOException {
    Socket socket = new Socket();
    track(socket);
    try {
      socket.connect(address, 10_000);
      socket.setSoTimeout(10_000);
      if (tls) {
        Socket raw = socket;
        socket =
            Tls.context(verifyTls)
                .getSocketFactory()
                .createSocket(raw, endpoint.getHost(), address.getPort(), true);
        track(socket);
        sockets.remove(raw);
        var secure = (SSLSocket) socket;
        var parameters = secure.getSSLParameters();
        parameters.setEndpointIdentificationAlgorithm(verifyTls ? "HTTPS" : null);
        secure.setSSLParameters(parameters);
        secure.startHandshake();
      }
      return socket;
    } catch (IOException | RuntimeException e) {
      release(socket);
      throw e;
    }
  }

  private void handle(Socket client) {
    boolean sent = false;
    try {
      client.setSoTimeout(10_000);
      String head = readHead(client.getInputStream());
      var headers = headers(head);
      boolean websocket =
          tokens(headers, "upgrade").stream().anyMatch("websocket"::equalsIgnoreCase)
              && tokens(headers, "connection").stream().anyMatch("upgrade"::equalsIgnoreCase);
      if (!websocket) {
        try (Socket upstream = connect(httpAddress, false)) {
          upstream.getOutputStream().write(head.getBytes(StandardCharsets.ISO_8859_1));
          sent = true;
          relayHttp(client, upstream);
        }
        return;
      }
      String[] request = head.substring(0, head.indexOf("\r\n")).split(" ");
      if (request.length != 3
          || !request[0].equals("GET")
          || !request[1].startsWith("/")
          || request[1].startsWith("//")
          || !request[2].equals("HTTP/1.1")
          || headers.containsKey("transfer-encoding")
          || headers.getOrDefault("content-length", List.of("0")).stream()
              .anyMatch(v -> !v.equals("0"))) throw new IOException("Invalid WebSocket upgrade");
      boolean tls = endpoint.getScheme().equals("https");
      int port = endpoint.getPort() == -1 ? (tls ? 443 : 80) : endpoint.getPort();
      for (int attempt = 0; attempt < 2; attempt++) {
        Socket upstream = connect(new InetSocketAddress(endpoint.getHost(), port), tls);
        try {
          var protocols = new LinkedHashSet<String>();
          for (String protocol : tokens(headers, "sec-websocket-protocol"))
            if (!protocol.isEmpty() && !protocol.startsWith("lambda-microvms"))
              protocols.add(protocol);
          String auth = token.get();
          if (!auth.matches("[!#$%&'*+.^_`|~0-9A-Za-z-]+"))
            throw new IOException("Invalid auth token");
          protocols.add("lambda-microvms");
          protocols.add("lambda-microvms.authentication." + auth);
          protocols.add("lambda-microvms.port." + remotePort);
          String path =
              Objects.toString(endpoint.getRawPath(), "").replaceAll("/$", "") + request[1];
          var outgoing =
              new StringBuilder(
                  "GET " + path + " HTTP/1.1\r\nHost: " + endpoint.getRawAuthority() + "\r\n");
          var excluded =
              new HashSet<>(
                  tokens(headers, "connection").stream()
                      .map(v -> v.toLowerCase(Locale.ROOT))
                      .toList());
          excluded.addAll(
              Set.of(
                  "host",
                  "connection",
                  "upgrade",
                  "sec-websocket-protocol",
                  "content-length",
                  "x-aws-proxy-auth",
                  "x-aws-proxy-port",
                  "proxy-authorization",
                  "proxy-connection"));
          headers.forEach(
              (name, values) -> {
                if (!excluded.contains(name))
                  for (String value : values)
                    outgoing.append(name).append(": ").append(value).append("\r\n");
              });
          outgoing
              .append("Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Protocol: ")
              .append(String.join(", ", protocols))
              .append("\r\n\r\n");
          upstream
              .getOutputStream()
              .write(outgoing.toString().getBytes(StandardCharsets.ISO_8859_1));
          String response = readHead(upstream.getInputStream());
          int status = Integer.parseInt(response.split(" ", 3)[1]);
          if (status == 401 && attempt == 0) continue;
          if (status != 101) {
            client
                .getOutputStream()
                .write(
                    ("HTTP/1.1 "
                            + status
                            + " Upgrade rejected\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                        .getBytes(StandardCharsets.US_ASCII));
            return;
          }
          // Auth/routing protocols belong only to the upstream leg, never to browser negotiation.
          var responseHeaders = headers(response);
          var downstream = new StringBuilder("HTTP/1.1 101 Switching Protocols\r\n");
          responseHeaders.forEach(
              (name, values) -> {
                if (name.equals("x-aws-proxy-auth") || name.equals("x-aws-proxy-port")) return;
                for (String value : values) {
                  if (name.equals("sec-websocket-protocol") && value.startsWith("lambda-microvms"))
                    continue;
                  downstream.append(name).append(": ").append(value).append("\r\n");
                }
              });
          downstream.append("\r\n");
          client
              .getOutputStream()
              .write(downstream.toString().getBytes(StandardCharsets.ISO_8859_1));
          sent = true;
          relay(client, upstream);
          return;
        } finally {
          release(upstream);
        }
      }
    } catch (Exception e) {
      if (!sent)
        try {
          client
              .getOutputStream()
              .write(
                  "HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
                      .getBytes(StandardCharsets.US_ASCII));
        } catch (IOException ignored) {
        }
    } finally {
      release(client);
    }
  }

  private void relayHttp(Socket client, Socket upstream) throws Exception {
    client.setSoTimeout(0);
    upstream.setSoTimeout(0);
    var response = workers.submit(() -> pump(upstream, client));
    try {
      client.getInputStream().transferTo(upstream.getOutputStream());
      // A client can finish sending while it is still waiting for its HTTP response.
      if (!upstream.isClosed()) upstream.shutdownOutput();
      response.get();
    } catch (IOException e) {
      if (!upstream.isClosed()) throw e;
    } finally {
      release(upstream);
    }
  }

  private void relay(Socket client, Socket upstream) throws IOException {
    client.setSoTimeout(0);
    upstream.setSoTimeout(0);
    workers.submit(() -> pump(upstream, client));
    pump(client, upstream);
  }

  private void pump(Socket source, Socket destination) {
    try {
      source.getInputStream().transferTo(destination.getOutputStream());
    } catch (IOException ignored) {
    } finally {
      release(source);
      release(destination);
    }
  }

  @Override
  public void close() {
    closed = true;
    try {
      listener.close();
    } catch (IOException ignored) {
    }
    sockets.forEach(this::release);
    workers.shutdownNow();
  }
}
