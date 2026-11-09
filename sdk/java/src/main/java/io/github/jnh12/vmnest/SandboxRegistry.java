package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.*;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.TimeoutException;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

/** Persistent sandbox names, endpoints and regions, compatible with the Python state store. */
public final class SandboxRegistry implements AutoCloseable {
  private final FileChannel lockChannel;
  private final java.nio.channels.FileLock fileLock;

  public record Entry(
      String name,
      String microvmId,
      int agentPort,
      String endpoint,
      String imageArn,
      String region) {
    public Entry(String name, String microvmId, int agentPort) {
      this(name, microvmId, agentPort, null, null, null);
    }
  }

  private final Map<String, ControlPlane> regional = new HashMap<>();
  private java.util.function.Function<String, ControlPlane> regionResolver;

  public static Path defaultPath() {
    return Path.of(
        System.getenv()
            .getOrDefault(
                "AGENT_SANDBOX_STATE",
                Path.of(System.getProperty("user.home"), ".agent_sandbox", "state.json")
                    .toString()));
  }

  public SandboxRegistry(ControlPlane control) throws IOException {
    this(defaultPath(), control);
  }

  public SandboxRegistry(
      Path file, ControlPlane control, java.util.function.Function<String, ControlPlane> resolver)
      throws IOException {
    this(file, control);
    regionResolver = resolver;
  }

  private String region() {
    if (control.hosted != null) return "us-east-1";
    var config = control.aws().serviceClientConfiguration();
    return config == null || config.region() == null ? null : config.region().id();
  }

  private ControlPlane forEntry(Entry entry) {
    if (control.hosted != null) return control;
    if (entry.region() == null || entry.region().equals(region())) return control;
    if (regionResolver != null) return regionResolver.apply(entry.region());
    return regional.computeIfAbsent(
        entry.region(),
        r -> {
          var builder =
              software.amazon.awssdk.services.lambdamicrovms.LambdaMicrovmsClient.builder()
                  .region(software.amazon.awssdk.regions.Region.of(r));
          var config = control.aws().serviceClientConfiguration();
          if (config != null) builder.credentialsProvider(config.credentialsProvider());
          return new ControlPlane(builder.build());
        });
  }

  private final Path file;
  private final ControlPlane control;
  private final ObjectMapper json = new ObjectMapper();

  public SandboxRegistry(Path file, ControlPlane control) throws IOException {
    this.file = file.toAbsolutePath();
    this.control = control;
    if (control.hosted != null) {
      lockChannel = null;
      fileLock = null;
      return;
    }
    Files.createDirectories(this.file.getParent());
    lockChannel =
        FileChannel.open(
            this.file.resolveSibling(this.file.getFileName() + ".lock"),
            StandardOpenOption.CREATE,
            StandardOpenOption.WRITE);
    try {
      fileLock = lockChannel.tryLock();
      if (fileLock == null) throw new IOException("Registry already open");
    } catch (Exception e) {
      lockChannel.close();
      throw new IOException("Registry already open or unavailable", e);
    }
  }

  @Override
  public void close() throws IOException {
    regional.values().forEach(cp -> cp.aws().close());
    if (fileLock != null) fileLock.release();
    if (lockChannel != null) lockChannel.close();
  }

  private Map<String, Entry> load() throws IOException {
    if (control.hosted != null) {
      var entries = new TreeMap<String, Entry>();
      for (var item : control.hosted.sandboxes()) {
        if ("terminated".equals(item.path("state").asText())) continue;
        String name = item.path("name").asText();
        entries.put(name, new Entry(name, item.path("id").asText(), Ports.agent(),
            item.path("endpoint").asText(null), item.path("image").asText("default"), "us-east-1"));
      }
      return entries;
    }
    if (!Files.exists(file)) return new TreeMap<>();
    var map = new TreeMap<String, Entry>();
    var root = json.readTree(file.toFile());
    if (root.isArray()) {
      for (var entry : json.treeToValue(root, Entry[].class)) map.put(entry.name(), entry);
    } else {
      var records = root.path("sandboxes").fields();
      while (records.hasNext()) {
        var record = records.next();
        var value = record.getValue();
        var entry =
            new Entry(
                record.getKey(),
                value.path("microvm_id").asText(),
                root.path("java_agent_ports").path(record.getKey()).asInt(Ports.agent()),
                value.path("endpoint").asText(null),
                value.path("image_arn").asText(null),
                value.path("region").asText(null));
        map.put(entry.name(), entry);
      }
    }
    return map;
  }

  private void save(Map<String, Entry> entries) throws IOException {
    if (control.hosted != null) return;
    Files.createDirectories(file.getParent());
    Path tmp = Files.createTempFile(file.getParent(), "registry-", ".tmp");
    try {
      var root = json.createObjectNode();
      var sandboxes = root.putObject("sandboxes");
      var ports = root.putObject("java_agent_ports");
      for (var entry : entries.values()) {
        sandboxes
            .putObject(entry.name())
            .put("name", entry.name())
            .put("microvm_id", entry.microvmId())
            .put("endpoint", entry.endpoint())
            .put("image_arn", entry.imageArn())
            .put("region", entry.region());
        ports.put(entry.name(), entry.agentPort());
      }
      json.writerWithDefaultPrettyPrinter().writeValue(tmp.toFile(), root);
      try {
        Files.move(tmp, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
      } catch (AtomicMoveNotSupportedException e) {
        Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING);
      }
    } finally {
      Files.deleteIfExists(tmp);
    }
  }

  public synchronized List<Entry> list() throws IOException {
    return List.copyOf(load().values());
  }

  public synchronized Map<String, String> statusAll() throws IOException {
    var out = new LinkedHashMap<String, String>();
    for (var entry : list()) {
      try {
        out.put(
            entry.name(),
            Sandbox.normalizedState(
                forEntry(entry)
                    .aws()
                    .getMicrovm(r -> r.microvmIdentifier(entry.microvmId()))
                    .stateAsString()));
      } catch (RuntimeException e) {
        out.put(entry.name(), "unreachable");
      }
    }
    return out;
  }

  public synchronized Sandbox get(String name) throws IOException {
    var entry = load().get(name);
    if (entry == null) throw new IllegalArgumentException("Unknown sandbox: " + name);
    return Sandbox.attach(
        name,
        entry.microvmId(),
        forEntry(entry),
        entry.agentPort(),
        Duration.ofMinutes(5),
        entry.endpoint());
  }

  public synchronized void attach(String name, String id, int port) throws IOException {
    if (control.hosted != null) {
      var entry = load().get(name);
      if (entry == null || !entry.microvmId().equals(id))
        throw new IllegalArgumentException("Use the sandbox's hosted name; aliases are local-only");
      return;
    }
    var entries = load();
    if (entries.containsKey(name)) throw new IllegalArgumentException("Duplicate name");
    if (name == null
        || name.isBlank()
        || name.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 128
        || port < 1
        || port > 65535
        || port == Ports.hook())
      throw new IllegalArgumentException("Invalid sandbox configuration");
    var info = control.aws().getMicrovm(r -> r.microvmIdentifier(id));
    entries.put(name, new Entry(name, id, port, info.endpoint(), info.imageArn(), region()));
    save(entries);
  }

  public synchronized Sandbox create(
      String name, RunMicrovmRequest request, int port, Duration wait)
      throws IOException, InterruptedException, TimeoutException {
    if (name == null
        || name.isBlank()
        || name.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 128
        || port < 1
        || port > 65535
        || port == Ports.hook()
        || wait.isNegative()
        || wait.isZero()) throw new IllegalArgumentException("Invalid sandbox configuration");
    var entries = load();
    if (entries.containsKey(name)) throw new IllegalArgumentException("Duplicate name");
    var b = request.toBuilder();
    if (request.clientToken() == null) b.clientToken(UUID.randomUUID().toString());
    String id = control.runMicrovm(b.build(), name).microvmId();
    entries.put(name, new Entry(name, id, port, null, request.imageIdentifier(), region()));
    try {
      save(entries);
    } catch (IOException e) {
      try {
        control.aws().terminateMicrovm(r -> r.microvmIdentifier(id));
      } catch (RuntimeException cleanup) {
        e.addSuppressed(cleanup);
      }
      throw e;
    }
    control.waitUntilRunning(id, wait, Duration.ofSeconds(1));
    return get(name);
  }

  public synchronized void remove(String name) throws IOException {
    var entries = load();
    var entry = entries.get(name);
    if (entry == null) throw new IllegalArgumentException("Unknown sandbox");
    try {
      forEntry(entry).aws().terminateMicrovm(r -> r.microvmIdentifier(entry.microvmId()));
    } catch (ResourceNotFoundException ignored) {
    }
    entries.remove(name);
    save(entries);
  }

  public ExecResult run(String name, RunMicrovmRequest request, String command, Duration timeout)
      throws Exception {
    try (var sandbox =
        Sandbox.create(
            name,
            control,
            request,
            Ports.agent(),
            Duration.ofMinutes(2),
            timeout.plusSeconds(30))) {
      return sandbox.exec("bash", List.of("-lc", command), null, null, timeout);
    }
  }
}
