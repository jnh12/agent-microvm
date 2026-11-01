package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.*;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.TimeoutException;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

/** Persistent names for a single application process. AWS client region must match the registry. */
public final class SandboxRegistry implements AutoCloseable {
  private final FileChannel lockChannel;
  private final java.nio.channels.FileLock fileLock;

  public record Entry(String name, String microvmId, int agentPort) {}

  private final Path file;
  private final ControlPlane control;
  private final ObjectMapper json = new ObjectMapper();

  public SandboxRegistry(Path file, ControlPlane control) throws IOException {
    this.file = file.toAbsolutePath();
    this.control = control;
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
    fileLock.release();
    lockChannel.close();
  }

  private Map<String, Entry> load() throws IOException {
    if (!Files.exists(file)) return new TreeMap<>();
    var map = new TreeMap<String, Entry>();
    for (var entry : json.readValue(Files.readAllBytes(file), Entry[].class))
      map.put(entry.name(), entry);
    return map;
  }

  private void save(Map<String, Entry> entries) throws IOException {
    Files.createDirectories(file.getParent());
    Path tmp = Files.createTempFile(file.getParent(), "registry-", ".tmp");
    try {
      json.writeValue(tmp.toFile(), entries.values());
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
                control
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
        name, entry.microvmId(), control, entry.agentPort(), Duration.ofMinutes(5));
  }

  public synchronized void attach(String name, String id, int port) throws IOException {
    var entries = load();
    if (entries.containsKey(name)) throw new IllegalArgumentException("Duplicate name");
    if (name == null
        || name.isBlank()
        || name.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 128
        || port < 1
        || port > 65535
        || port == Ports.hook())
      throw new IllegalArgumentException("Invalid sandbox configuration");
    control.aws().getMicrovm(r -> r.microvmIdentifier(id));
    entries.put(name, new Entry(name, id, port));
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
    String id = control.aws().runMicrovm(b.build()).microvmId();
    entries.put(name, new Entry(name, id, port));
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
    control.aws().terminateMicrovm(r -> r.microvmIdentifier(entry.microvmId()));
    entries.remove(name);
    save(entries);
  }

  public ExecResult run(String name, RunMicrovmRequest request, String command, Duration timeout)
      throws Exception {
    try (var sandbox =
        Sandbox.create(
            name, control, request, 8080, Duration.ofMinutes(2), timeout.plusSeconds(30))) {
      return sandbox.exec("bash", List.of("-lc", command), null, null, timeout);
    }
  }
}
