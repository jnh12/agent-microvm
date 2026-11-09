package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.Duration;
import java.util.*;
import java.util.function.Consumer;
import java.util.concurrent.TimeoutException;

/** Hosted agent profiles, disposable prompt jobs, durable agents and webhooks. */
public final class Agents {
  private static final Set<String> RESOURCES = Set.of(
      "credentials", "profiles", "connections", "agents", "runs", "webhooks");
  private static final Map<String, Set<String>> ACTIONS = Map.of(
      "credentials", Set.of("verify", "connections/discover"),
      "agents", Set.of("start", "stop"), "runs", Set.of("cancel"),
      "webhooks", Set.of("rotate", "test"));
  private final HostedClient client;

  public Agents(HostedClient client) { this.client = Objects.requireNonNull(client); }
  public static Agents fromEnvironment() { return HostedClient.fromEnvironment().agents(); }

  private static String path(String resource, String id) {
    if (!RESOURCES.contains(resource)) throw new IllegalArgumentException("Unknown agent resource");
    return "/v1/" + resource + (id == null ? "" : "/" + HostedClient.part(id));
  }
  public JsonNode list(String resource) { return list(resource, 100, 0); }
  public JsonNode list(String resource, int limit, int offset) {
    if (limit < 1 || limit > 100 || offset < 0) throw new IllegalArgumentException("Invalid page");
    return client.request("GET", path(resource, null) + "?limit=" + limit + "&offset=" + offset,
        null, null);
  }
  public JsonNode get(String resource, String id) {
    return client.request("GET", path(resource, id), null, null);
  }
  public JsonNode create(String resource, Map<String, ?> fields) {
    return client.request("POST", path(resource, null), fields,
        "runs".equals(resource) ? UUID.randomUUID().toString() : null);
  }
  public JsonNode update(String resource, String id, Map<String, ?> fields) {
    return client.request("PATCH", path(resource, id), fields, null);
  }
  public JsonNode delete(String resource, String id) {
    return client.request("DELETE", path(resource, id), null, null);
  }
  public JsonNode action(String resource, String id, String action) {
    return action(resource, id, action, Map.of());
  }
  public JsonNode action(String resource, String id, String action, Map<String, ?> fields) {
    if (!ACTIONS.getOrDefault(resource, Set.of()).contains(action))
      throw new IllegalArgumentException("Unknown resource action");
    return client.request("POST", path(resource, id) + "/" + action, fields, null);
  }
  public JsonNode run(String prompt, String profileId) {
    return run(prompt, profileId, null, null, null);
  }
  public JsonNode run(String prompt, String profileId, String agentId, String name, String retryKey) {
    if ((profileId == null) == (agentId == null))
      throw new IllegalArgumentException("Provide exactly one of profileId and agentId");
    var body = new LinkedHashMap<String, Object>();
    body.put("prompt", prompt);
    body.put(profileId != null ? "profile_id" : "agent_id", profileId != null ? profileId : agentId);
    if (name != null) body.put("name", name);
    return client.request("POST", "/v1/runs", body,
        retryKey == null ? UUID.randomUUID().toString() : retryKey);
  }
  public JsonNode message(String agentId, String text) { return message(agentId, text, null); }
  public JsonNode message(String agentId, String text, String retryKey) {
    return client.request("POST", path("agents", agentId) + "/messages", Map.of("text", text),
        retryKey == null ? UUID.randomUUID().toString() : retryKey);
  }
  public JsonNode messages(String agentId) {
    return client.request("GET", path("agents", agentId) + "/messages", null, null);
  }
  public JsonNode events(String runId, long after) {
    if (after < 0) throw new IllegalArgumentException("Invalid event cursor");
    return client.request("GET", path("runs", runId) + "/events?after=" + after, null, null);
  }
  public void follow(String runId, long after, Duration timeout, Consumer<JsonNode> event)
      throws InterruptedException, TimeoutException {
    long deadline = System.nanoTime() + timeout.toNanos();
    while (System.nanoTime() < deadline) {
      JsonNode page = events(runId, after);
      for (JsonNode item : page.path("items")) {
        if (item.path("id").asLong() > after) {
          after = item.path("id").asLong();
          event.accept(item);
        }
      }
      after = Math.max(after, page.path("next_cursor").asLong(after));
      if (page.path("complete").asBoolean()) return;
      Thread.sleep(1000);
    }
    throw new TimeoutException("Run is still active; reconnect using its run ID");
  }
  public JsonNode artifacts(String runId) {
    return client.request("GET", path("runs", runId) + "/artifacts", null, null);
  }
  public JsonNode artifactUrl(String runId, String artifactId) {
    return client.request("GET", path("runs", runId) + "/artifacts/"
        + HostedClient.part(artifactId) + "/download", null, null);
  }
  public JsonNode connectionTools(String connectionId) {
    return client.request("GET", path("connections", connectionId) + "/tools", null, null);
  }
}
