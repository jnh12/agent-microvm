package io.github.jnh12.vmnest;

import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.TimeoutException;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

/** Blocking Java equivalent of Python Sandbox. close/stop terminate; suspend preserves the VM. */
public final class Sandbox implements AutoCloseable {
  private final String name, id;
  private final ControlPlane control;
  private final int port;
  private final Duration requestTimeout;
  private GetMicrovmResponse info;
  private AgentClient agent;
  private boolean terminated;

  private Sandbox(String name, String id, ControlPlane control, int port, Duration requestTimeout) {
    if (name == null || name.isBlank() || name.getBytes(StandardCharsets.UTF_8).length > 128)
      throw new IllegalArgumentException("Name must be 1–128 UTF-8 bytes");
    if (port < 1 || port > 65535 || port == 9000)
      throw new IllegalArgumentException("Invalid or reserved agent port");
    if (requestTimeout.isNegative() || requestTimeout.isZero())
      throw new IllegalArgumentException("Positive request timeout required");
    this.name = name;
    this.id = id;
    this.control = control;
    this.port = port;
    this.requestTimeout = requestTimeout;
  }

  public static Sandbox create(
      String name,
      ControlPlane control,
      RunMicrovmRequest request,
      int port,
      Duration waitTimeout,
      Duration requestTimeout)
      throws InterruptedException, TimeoutException {
    // Validate before launching a billable resource.
    new Sandbox(name, "validation", control, port, requestTimeout);
    if (waitTimeout.isZero() || waitTimeout.isNegative())
      throw new IllegalArgumentException("Positive wait timeout required");
    var builder = request.toBuilder();
    if (request.imageIdentifier() == null)
      builder.imageIdentifier(System.getenv("AGENT_SANDBOX_IMAGE_ARN"));
    if (request.executionRoleArn() == null)
      builder.executionRoleArn(System.getenv("AGENT_SANDBOX_EXECUTION_ROLE_ARN"));
    if (request.idlePolicy() == null)
      builder.idlePolicy(
          p -> p.maxIdleDurationSeconds(900).suspendedDurationSeconds(300).autoResumeEnabled(true));
    if (request.clientToken() == null) builder.clientToken(UUID.randomUUID().toString());
    var resolved = builder.build();
    if (resolved.imageIdentifier() == null || resolved.executionRoleArn() == null)
      throw new IllegalArgumentException("Image and execution role required");
    String id = control.aws().runMicrovm(resolved).microvmId();
    var sandbox = new Sandbox(name, id, control, port, requestTimeout);
    // On wait failure the exception includes the ID for reconciliation; do not launch again
    // blindly.
    sandbox.info = control.waitUntilRunning(id, waitTimeout, Duration.ofSeconds(1));
    sandbox.connect();
    return sandbox;
  }

  public static Sandbox attach(
      String name, String id, ControlPlane control, int port, Duration requestTimeout) {
    var sandbox = new Sandbox(name, id, control, port, requestTimeout);
    sandbox.inspect();
    sandbox.connect();
    return sandbox;
  }

  private void connect() {
    String endpoint = info.endpoint();
    if (endpoint == null || endpoint.isBlank()) {
      agent = null;
      return;
    }
    URI uri = URI.create(endpoint.contains("://") ? endpoint : "https://" + endpoint);
    if (!uri.getScheme().equals("https"))
      throw new IllegalStateException("AWS endpoint must use HTTPS");
    agent =
        new AgentClient(
            uri,
            () ->
                control
                    .aws()
                    .createMicrovmAuthToken(
                        r ->
                            r.microvmIdentifier(id)
                                .expirationInMinutes(30)
                                .allowedPorts(p -> p.port(port)))
                    .authToken()
                    .get("X-aws-proxy-auth"),
            port,
            requestTimeout);
  }

  public String name() {
    return name;
  }

  public String microvmId() {
    return id;
  }

  public String endpoint() {
    return info.endpoint();
  }

  public String state() {
    return switch (Objects.toString(info.stateAsString(), "")) {
      case "PENDING", "STARTING", "RESUMING" -> "pending";
      case "RUNNING", "SUSPENDING" -> "running";
      case "SUSPENDED" -> "suspended";
      case "TERMINATING", "TERMINATED" -> "stopped";
      case "FAILED" -> "failed";
      default -> "unknown";
    };
  }

  public GetMicrovmResponse inspect() {
    info = control.aws().getMicrovm(r -> r.microvmIdentifier(id));
    return info;
  }

  public String refresh() {
    inspect();
    return state();
  }

  public AgentClient agent() {
    if (agent == null || terminated) throw new IllegalStateException("Guest unavailable");
    return agent;
  }

  public ExecResult exec(
      String command, List<String> args, String cwd, Map<String, String> env, Duration timeout)
      throws IOException, InterruptedException {
    return agent().exec(command, args, cwd, env, timeout);
  }

  public byte[] readFile(String path) throws IOException, InterruptedException {
    return agent().readFile(path);
  }

  public void writeFile(String path, byte[] content) throws IOException, InterruptedException {
    agent().writeFile(path, content);
  }

  public void suspend() {
    control.aws().suspendMicrovm(r -> r.microvmIdentifier(id));
  }

  public void start() throws InterruptedException, TimeoutException {
    resume();
  }

  public void resume() throws InterruptedException, TimeoutException {
    control.aws().resumeMicrovm(r -> r.microvmIdentifier(id));
    info = control.waitUntilRunning(id, Duration.ofSeconds(120), Duration.ofSeconds(1));
    connect();
  }

  public void terminate() {
    if (!terminated) {
      control.aws().terminateMicrovm(r -> r.microvmIdentifier(id));
      terminated = true;
      agent = null;
    }
  }

  public void kill() {
    terminate();
  }

  public void stop() {
    terminate();
  }

  @Override
  public void close() {
    terminate();
  }
}
