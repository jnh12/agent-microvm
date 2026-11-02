package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.dataformat.yaml.YAMLMapper;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import software.amazon.awssdk.services.lambdamicrovms.model.CreateMicrovmImageRequest;

/** Reads the same sandbox.yaml schema and multi-document configurations as the Python SDK. */
public final class InfraConfig {
  private final JsonNode data;
  private final Path base;
  private Path guest;

  public InfraConfig(JsonNode data, Path base) {
    if (!data.isObject()) throw new IllegalArgumentException("Configuration must be a mapping");
    this.data = data.deepCopy();
    this.base = base.toAbsolutePath();
    if (project().isBlank() || stack().isBlank() || region().isBlank())
      throw new IllegalArgumentException("project, stack and region must be non-empty");
  }

  private String text(String pointer, String fallback) {
    return data.at(pointer).asText(fallback);
  }

  private List<String> strings(String pointer) {
    var out = new ArrayList<String>();
    data.at(pointer).forEach(n -> out.add(n.asText()));
    return List.copyOf(out);
  }

  public String project() {
    return text("/project", "agent-microvm");
  }

  public String stack() {
    return text("/stack", "dev");
  }

  public String region() {
    return text("/region", "us-east-1");
  }

  public static Path defaultPath() {
    String override = System.getenv("AGENT_SANDBOX_SETUP");
    return override != null && !override.isBlank()
        ? Path.of(override)
        : Files.isRegularFile(Path.of("sandbox.yaml"))
            ? Path.of("sandbox.yaml")
            : Files.isRegularFile(Path.of("setup.yaml"))
                ? Path.of("setup.yaml")
                : Path.of("sandbox.yaml");
  }

  public static Path defaultStatePath() {
    return Path.of(
        System.getenv()
            .getOrDefault(
                "AGENT_SANDBOX_INFRA_STATE",
                Path.of(System.getProperty("user.home"), ".agent_sandbox", "infra-state.json")
                    .toString()));
  }

  public static InfraConfig load(Path file) throws IOException {
    Path actual = file == null ? defaultPath() : file;
    var tree = new YAMLMapper().readTree(actual.toFile());
    var config =
        new InfraConfig(
            tree == null || tree.isNull() ? new ObjectMapper().createObjectNode() : tree,
            actual.toAbsolutePath().getParent());
    config.guestDirectory();
    return config;
  }

  public static List<InfraConfig> loadAll(List<Path> paths, String stackOverride)
      throws IOException {
    var files = new ArrayList<Path>();
    for (Path path : paths == null || paths.isEmpty() ? List.of(defaultPath()) : paths) {
      if (Files.isDirectory(path)) {
        try (var entries = Files.list(path)) {
          var yaml =
              entries
                  .filter(Files::isRegularFile)
                  .filter(p -> p.toString().endsWith(".yaml") || p.toString().endsWith(".yml"))
                  .sorted()
                  .toList();
          if (yaml.isEmpty()) throw new IOException("No YAML configurations in " + path);
          files.addAll(yaml);
        }
      } else files.add(path);
    }
    var result = new ArrayList<InfraConfig>();
    var mapper = new YAMLMapper();
    for (Path file : files) {
      int before = result.size();
      try (var documents = mapper.readerFor(JsonNode.class).<JsonNode>readValues(file.toFile())) {
        while (documents.hasNextValue()) {
          JsonNode node = documents.nextValue();
          if (node == null || node.isNull()) continue;
          if (!node.isObject()) throw new IOException("Configuration must be a mapping: " + file);
          if (stackOverride != null && !stackOverride.isEmpty())
            ((com.fasterxml.jackson.databind.node.ObjectNode) node).put("stack", stackOverride);
          var config = new InfraConfig(node, file.toAbsolutePath().getParent());
          config.guestDirectory();
          result.add(config);
        }
      }
      if (result.size() == before) throw new IOException("No config documents found: " + file);
    }
    return List.copyOf(result);
  }

  public synchronized Path guestDirectory() throws IOException {
    if (guest == null) {
      Path relative = Path.of(text("/image/guest_dir", "./guest"));
      guest =
          Images.resolveGuestDirectory(
              base.resolve(relative).normalize(), relative.normalize().equals(Path.of("guest")));
    }
    return guest;
  }

  public Infrastructure.Spec spec(Map<String, String> outputs) throws IOException {
    String role = text("/role/arn", "");
    String bucket = text("/bucket/name", "");
    boolean reuse = !bucket.isBlank();
    if (!reuse)
      bucket =
          outputs.getOrDefault(
              "build_bucket",
              (project() + "-" + stack())
                      .toLowerCase(Locale.ROOT)
                      .replaceAll("[^a-z0-9-]", "-")
                      .substring(0, Math.min(25, (project() + "-" + stack()).length()))
                  + "-"
                  + UUID.randomUUID());
    var image =
        CreateMicrovmImageRequest.builder().name(text("/image/name", "agent-sandbox-guest"));
    String baseArn = text("/image/base_image_arn", ""),
        baseVersion = text("/image/base_image_version", "");
    if (!baseArn.isBlank()) image.baseImageArn(baseArn);
    if (!baseVersion.isBlank()) image.baseImageVersion(baseVersion);
    String connector = text("/network/egress/connector_arn", "");
    return new Infrastructure.Spec(
        region(),
        role.isBlank() ? null : role,
        role.isBlank() ? Networking.operatorRole(text("/role/name", "agent-sandbox-exec")) : null,
        strings("/role/extra_policy_arns"),
        bucket,
        reuse,
        guestDirectory(),
        image.build(),
        connector.isBlank() ? null : connector,
        null);
  }

  public Infrastructure.NetworkSpec network() {
    if (!text("/network/egress/connector_arn", "").isBlank()) return null;
    String vpc = text("/network/egress/vpc_id", ""),
        group = text("/network/egress/security_group_id", ""),
        role = text("/network/egress/operator_role_arn", "");
    var subnets = strings("/network/egress/subnet_ids");
    if (vpc.isBlank() && group.isBlank() && role.isBlank() && subnets.isEmpty()) return null;
    return new Infrastructure.NetworkSpec(
        text("/network/egress/name", "agent-sandbox-egress"), vpc, subnets, group, role);
  }
}
