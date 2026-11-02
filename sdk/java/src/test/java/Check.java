import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import software.amazon.awssdk.auth.credentials.*;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

public class Check {
  static final ObjectMapper json = new ObjectMapper();

  static void check(boolean value, String what) {
    if (!value) throw new AssertionError(what);
  }

  public static void main(String[] args) throws Exception {
    var received = new AtomicReference<String>();
    var auth = new AtomicReference<String>();
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        x -> {
          received.set(new String(x.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
          auth.set(x.getRequestHeaders().getFirst("Authorization"));
          String path = x.getRequestURI().getPath();
          String result =
              path.endsWith("auth-token")
                  ? "{\"authToken\":{\"X-aws-proxy-auth\":\"test-token\"}}"
                  : "{\"microvmId\":\"mvm-test\",\"state\":\"RUNNING\",\"endpoint\":\"test.invalid\"}";
          byte[] data = result.getBytes(StandardCharsets.UTF_8);
          x.getResponseHeaders().set("Content-Type", "application/json");
          x.sendResponseHeaders(200, data.length);
          x.getResponseBody().write(data);
          x.close();
        });
    server.start();
    try (var client =
        LambdaMicrovmsClient.builder()
            .region(Region.US_EAST_1)
            .credentialsProvider(
                StaticCredentialsProvider.create(
                    AwsBasicCredentials.create("fake-key", "fake-secret")))
            .endpointOverride(URI.create("http://127.0.0.1:" + server.getAddress().getPort()))
            .httpClientBuilder(UrlConnectionHttpClient.builder())
            .build()) {
      var result =
          client.runMicrovm(
              r ->
                  r.imageIdentifier("image-test")
                      .imageVersion("1")
                      .clientToken("repeat-safe-task-123")
                      .maximumDurationInSeconds(3600)
                      .idlePolicy(
                          p ->
                              p.maxIdleDurationSeconds(900)
                                  .suspendedDurationSeconds(300)
                                  .autoResumeEnabled(true))
                      .egressNetworkConnectors("egress-test")
                      .ingressNetworkConnectors("ingress-test"));
      check(result.microvmId().equals("mvm-test"), "response deserialization");
      check(
          json.readTree(received.get()).path("clientToken").asText().equals("repeat-safe-task-123"),
          "idempotency token");
      check(
          json.readTree(received.get()).path("idlePolicy").path("maxIdleDurationSeconds").asInt()
              == 900,
          "idle policy");
      check(auth.get().startsWith("AWS4-HMAC-SHA256 "), "AWS request signing");
      client.getMicrovm(r -> r.microvmIdentifier("mvm-test"));
      client.suspendMicrovm(r -> r.microvmIdentifier("mvm-test"));
      client.resumeMicrovm(r -> r.microvmIdentifier("mvm-test"));
      client.terminateMicrovm(r -> r.microvmIdentifier("mvm-test"));
      var token =
          client.createMicrovmAuthToken(
              r ->
                  r.microvmIdentifier("mvm-test")
                      .expirationInMinutes(30)
                      .allowedPorts(p -> p.port(8080)));
      check(token.authToken().get("X-aws-proxy-auth").equals("test-token"), "token map");
      check(
          json.readTree(received.get()).path("allowedPorts").get(0).path("port").asInt() == 8080,
          "port scope");
      var methods =
          Arrays.stream(LambdaMicrovmsClient.class.getMethods())
              .map(m -> m.getName())
              .collect(java.util.stream.Collectors.toSet());
      for (String name :
          List.of(
              "listMicrovmsPaginator",
              "createMicrovmImage",
              "getMicrovmImage",
              "updateMicrovmImage",
              "deleteMicrovmImage",
              "listMicrovmImagesPaginator",
              "createMicrovmShellAuthToken")) check(methods.contains(name), name);
      System.out.println(
          "PASS: official Java SDK resolves; lifecycle requests, SigV4, launch options,"
              + " idempotency, tokens, image APIs and pagination");
    } finally {
      server.stop(0);
    }
  }
}
