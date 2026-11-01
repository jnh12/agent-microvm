import static org.junit.jupiter.api.Assertions.*;

import io.github.jnh12.vmnest.*;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;
import org.junit.jupiter.api.Test;

/** Fails when Python adds an MCP operation without a reviewed Java mapping. */
class CoverageTest {
  @Test
  void allPythonOperationsHaveCallableJavaMappings() throws Exception {
    Map<String, String> mapping = new LinkedHashMap<>();
    add(mapping, RuntimeConfig.class, "check", "runtime_check");
    add(mapping, RuntimeConfig.class, "installationHelp", "runtime_install");
    add(mapping, Infrastructure.class, "outputs", "infra_outputs");
    add(mapping, Infrastructure.class, "list", "infra_list");
    add(mapping, SandboxRegistry.class, "run", "sandbox_run");
    add(mapping, SandboxRegistry.class, "create", "sandbox_create");
    add(mapping, Sandbox.class, "start", "sandbox_start");
    add(mapping, SandboxRegistry.class, "list", "sandbox_list");
    add(mapping, SandboxRegistry.class, "statusAll", "sandbox_status");
    add(mapping, Sandbox.class, "inspect", "sandbox_inspect");
    add(mapping, Sandbox.class, "suspend", "sandbox_stop");
    add(mapping, SandboxRegistry.class, "remove", "sandbox_remove");
    add(mapping, Sandbox.class, "waitFor", "sandbox_wait");
    add(mapping, Sandbox.class, "exec", "sandbox_exec");
    add(mapping, Sandbox.class, "shell", "sandbox_shell");
    for (String method :
        List.of("read", "write", "list", "mkdir", "remove", "copy", "rename", "stat", "exists"))
      add(mapping, SandboxFiles.class, method, "sandbox_fs_" + method);
    add(mapping, SandboxFiles.class, "copyFromHost", "sandbox_fs_copy_from_host");
    add(mapping, SandboxFiles.class, "copyToHost", "sandbox_fs_copy_to_host");
    add(mapping, Observability.class, "readLogs", "sandbox_logs_read");
    add(mapping, Observability.class, "pollLogs", "sandbox_logs_stream");
    add(mapping, Observability.class, "metrics", "sandbox_metrics");
    add(mapping, Observability.class, "metricsAll", "sandbox_metrics_all");
    add(mapping, Observability.class, "sampleMetrics", "sandbox_metrics_stream");
    for (String method : List.of("list", "inspect", "remove", "prune"))
      add(mapping, Images.class, method, "image_" + method);
    var upstream = new TreeSet<String>();
    var regex = Pattern.compile("async def ((?:sandbox_|image_|infra_|runtime_)[a-z_]+)\\(");
    try (var files = Files.list(Path.of("../sdk/agent_sandbox_mcp/tools"))) {
      for (var file : files.filter(p -> p.toString().endsWith(".py")).toList()) {
        var matches = regex.matcher(Files.readString(file));
        while (matches.find()) upstream.add(matches.group(1));
      }
    }
    assertEquals(upstream, mapping.keySet());
    assertEquals(35, upstream.size());
  }

  private void add(Map<String, String> map, Class<?> type, String method, String operation) {
    assertTrue(
        Arrays.stream(type.getMethods()).anyMatch(m -> m.getName().equals(method)),
        type.getName() + "." + method);
    map.put(operation, type.getSimpleName() + "." + method);
  }

  @Test
  void bundledGuestContainsActualAgent() throws Exception {
    Path root = Files.createTempDirectory("guest-test-");
    try {
      Images.extractBundledGuest(root);
      assertTrue(Files.exists(root.resolve("Dockerfile")));
      assertTrue(Files.readString(root.resolve("agentd/app.py")).contains("/v1/exec"));
    } finally {
      try (var paths = Files.walk(root)) {
        for (var path : paths.sorted(Comparator.reverseOrder()).toList()) Files.delete(path);
      }
    }
  }
}
