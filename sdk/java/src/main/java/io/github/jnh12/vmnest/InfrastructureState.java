package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;

/** Interoperable project/stack state; callers coordinate provisioning of each stack. */
final class InfrastructureState {
  private static final ObjectMapper JSON = new ObjectMapper();
  private static final Map<String, String> IDS =
      Map.of(
          "role",
          "role_name",
          "bucket",
          "build_bucket",
          "image",
          "image_arn",
          "connector",
          "egress_network_connector_arn",
          "security_group",
          "security_group_id",
          "network_operator_role",
          "network_operator_role_name");

  private static String resourceName(String name) {
    return name.equals("connector") ? "network_connector" : name;
  }

  static synchronized Infrastructure.State load(Path file, String stack) throws IOException {
    var raw = root(file).path("stacks").path(stack);
    var outputs = new LinkedHashMap<String, String>();
    raw.path("outputs")
        .fields()
        .forEachRemaining(
            e ->
                outputs.put(
                    e.getKey(),
                    e.getValue().isTextual() ? e.getValue().asText() : e.getValue().toString()));
    var managed = new HashSet<String>();
    for (String name : IDS.keySet()) {
      var resource = raw.path("resources").path(resourceName(name));
      if (resource.isMissingNode()) continue;
      if (resource.path("managed").asBoolean(true)) managed.add(name);
      outputs.put(IDS.get(name), resource.path("id").asText());
      if (name.equals("image") && resource.path("attrs").hasNonNull("version"))
        outputs.put("image_version", resource.path("attrs").path("version").asText());
      if (name.equals("role"))
        outputs.put(
            "execution_role_arn",
            resource.path("attrs").path("arn").asText(outputs.get("execution_role_arn")));
      if (name.equals("network_operator_role"))
        outputs.put(
            "network_operator_role_arn",
            resource.path("attrs").path("arn").asText(outputs.get("network_operator_role_arn")));
    }
    return new Infrastructure.State(outputs, managed);
  }

  static synchronized void save(Path file, String stack, Infrastructure.State state)
      throws IOException {
    ObjectNode root = root(file), stacks = root.withObject("stacks");
    ObjectNode value = stacks.withObject(stack);
    ObjectNode resources = value.withObject("resources");
    for (String name : IDS.keySet()) {
      String id = state.outputs().get(IDS.get(name));
      if (id == null) {
        resources.remove(resourceName(name));
        continue;
      }
      ObjectNode resource = resources.withObject(resourceName(name));
      resource.put("id", id).put("managed", state.managed().contains(name));
      String kind =
          switch (name) {
            case "role", "network_operator_role" -> "iam-role";
            case "bucket" -> "s3-bucket";
            case "image" -> "microvm-image";
            case "connector" -> "network-connector";
            default -> "ec2-sg";
          };
      resource.put("kind", kind);
      var attrs = resource.withObject("attrs");
      if (name.equals("role")) attrs.put("arn", state.outputs().get("execution_role_arn"));
      if (name.equals("network_operator_role"))
        attrs.put("arn", state.outputs().get("network_operator_role_arn"));
      if (name.equals("image")) attrs.put("version", state.outputs().get("image_version"));
    }
    var outputs = JSON.createObjectNode();
    for (var entry : state.outputs().entrySet()) {
      if (entry.getKey().equals("subnet_ids"))
        outputs.set(entry.getKey(), JSON.readTree(entry.getValue()));
      else outputs.put(entry.getKey(), entry.getValue());
    }
    value.set("outputs", outputs);
    value.remove("java_attached_policies");
    write(file, root);
  }

  static synchronized void clear(Path file, String stack) throws IOException {
    ObjectNode root = root(file);
    root.withObject("stacks").remove(stack);
    write(file, root);
  }

  static Map<String, Map<String, String>> list(Path file) throws IOException {
    var result = new TreeMap<String, Map<String, String>>();
    var names = root(file).path("stacks").fieldNames();
    while (names.hasNext()) {
      String name = names.next();
      result.put(name, Map.copyOf(load(file, name).outputs()));
    }
    return result;
  }

  private static ObjectNode root(Path file) throws IOException {
    if (!Files.exists(file)) return JSON.createObjectNode();
    JsonNode root = JSON.readTree(file.toFile());
    if (root == null || !root.isObject()) throw new IOException("Invalid infrastructure state");
    return (ObjectNode) root;
  }

  static void write(Path file, Object value) throws IOException {
    Files.createDirectories(file.toAbsolutePath().getParent());
    Path temporary = Files.createTempFile(file.toAbsolutePath().getParent(), "infra-", ".tmp");
    try {
      JSON.writerWithDefaultPrettyPrinter().writeValue(temporary.toFile(), value);
      try {
        Files.move(
            temporary, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
      } catch (AtomicMoveNotSupportedException e) {
        Files.move(temporary, file, StandardCopyOption.REPLACE_EXISTING);
      }
    } finally {
      Files.deleteIfExists(temporary);
    }
  }
}
