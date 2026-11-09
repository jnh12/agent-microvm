import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.sun.net.httpserver.HttpServer;
import io.github.jnh12.vmnest.*;
import java.net.*;
import java.time.Duration;
import java.util.*;
import org.junit.jupiter.api.Test;

class AgentsTest {
  @Test void hostedAgentResourcesAndRetryKeysNeedNoAws() throws Exception {
    var json = new ObjectMapper();
    var paths = new ArrayList<String>();
    var keys = new ArrayList<String>();
    var bodies = new ArrayList<JsonNode>();
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext("/", exchange -> {
      assertEquals("Bearer vmnest-only", exchange.getRequestHeaders().getFirst("Authorization"));
      paths.add(exchange.getRequestURI().toString());
      keys.add(exchange.getRequestHeaders().getFirst("Idempotency-Key"));
      byte[] input = exchange.getRequestBody().readAllBytes();
      bodies.add(input.length == 0 ? json.createObjectNode() : json.readTree(input));
      Object response = Map.of("id", "run", "state", "succeeded");
      if (exchange.getRequestURI().getPath().endsWith("/events"))
        response = Map.of("items", List.of(Map.of("id", 1, "kind", "result", "text", "done")),
            "next_cursor", 1, "complete", true);
      byte[] output = json.writeValueAsBytes(response);
      exchange.sendResponseHeaders(200, output.length);
      exchange.getResponseBody().write(output);
      exchange.close();
    });
    server.start();
    try (var hosted = new HostedClient(URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
        "vmnest-only")) {
      var agents = hosted.agents();
      agents.run("hello", "profile", null, null, "repeat-safe");
      agents.message("agent/id", "next", "message-safe");
      assertEquals("repeat-safe", keys.get(0));
      assertEquals("profile", bodies.get(0).path("profile_id").asText());
      assertFalse(bodies.get(0).has("agent_id"));
      assertEquals("/v1/agents/agent%2Fid/messages", paths.get(1));
      assertEquals("message-safe", keys.get(1));
      var events = new ArrayList<JsonNode>();
      agents.follow("run", 0, Duration.ofSeconds(2), events::add);
      assertEquals("done", events.getFirst().path("text").asText());
      assertThrows(IllegalArgumentException.class,
          () -> agents.run("bad", "profile", "agent", null, null));
      assertThrows(IllegalArgumentException.class, () -> agents.action("agents", "id", "exec"));
      agents.connectionTools("connection");
      assertEquals("/v1/connections/connection/tools", paths.getLast());
    } finally { server.stop(0); }
  }
}
