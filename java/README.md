# VMNest Java SDK

Java 21+ library for the same AWS MicroVMs and unchanged `agentd` guest as the Python SDK. No Spring, database, Docker, or external Python wrapper is required by the library.

Build and install locally (not yet published to Maven Central):

```sh
./gradlew test build publishToMavenLocal
```

Consumers use `mavenLocal()` and `implementation("io.github.jnh12:vmnest-java:0.1.0-SNAPSHOT")`.

```java
import io.github.jnh12.vmnest.*;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;
import java.time.Duration;
import java.util.List;

try (var aws = LambdaMicrovmsClient.create()) {
    var control = new ControlPlane(aws);
    var request = RunMicrovmRequest.builder()
        .imageIdentifier(System.getenv("AGENT_SANDBOX_IMAGE_ARN"))
        .executionRoleArn(System.getenv("AGENT_SANDBOX_EXECUTION_ROLE_ARN"))
        .clientToken("your-stable-task-id")
        .build();
    try (var sandbox = Sandbox.create("example", control, request, 8080,
            Duration.ofSeconds(120), Duration.ofMinutes(5))) {
        var result = sandbox.exec("python", List.of("-c", "print(1+1)"), null, null,
            Duration.ofSeconds(30));
        System.out.println(result.stdoutText());
    } // terminates VM, matching Python's async context manager
}
```

## Python SDK parity

| Python surface | Java equivalent |
|---|---|
| Sandbox create, attach, identity, endpoint, state | `Sandbox.create`, `attach`, `name`, `microvmId`, `endpoint`, `state` |
| exec (args/cwd/env/timeout), read_file, write_file | `exec`, `readFile`, `writeFile`; binary byte arrays |
| refresh, inspect | `refresh`, `inspect` (typed AWS response) |
| suspend, start/resume, stop/terminate/kill | Same names and lifecycle semantics |
| async context manager | `AutoCloseable`; close terminates |
| AgentClient health, token refresh, port routing | `AgentClient`; refresh once on 401, including health |
| ControlPlane run/get/list/suspend/resume/terminate | `control.aws()` typed AWS operations and paginators |
| auth tokens, create/list/managed-list/delete images | `control.aws()` typed AWS operations |
| wait_until_running | `waitUntilRunning` with timeout, interval, terminal-state checks |
| IdlePolicy / creation options | AWS `RunMicrovmRequest` and `IdlePolicy` builders |
| ExecResult | `ExecResult` with byte arrays, UTF-8 accessors, success |

The AWS client additionally exposes image version/build/tag APIs, idempotency tokens, and shell tokens. Use its standard credential/region provider chain; the library never stores credentials. Image and role default to the same environment variables as Python. Default idle policy matches Python (900/300 seconds, auto-resume). CPU/memory are image settings, not launch-time resizing options.

This is **SDK parity**, not a second implementation of the Python CLI, MCP server, local name registry, or `sandbox.yaml` infrastructure provisioner. Keep using `vmnest infra`/`hooklet-infra` to prepare images. Logs, metrics and infrastructure provisioning can use the corresponding native AWS SDK modules; CLI-only filesystem helpers can be composed using exec. No PTY/streaming execution or content snapshots are added to the guest.

## Operational behavior

- Calls block; use Java virtual threads when integrating with concurrent applications. Sandbox handles are not thread-safe; coordinate lifecycle changes per VM.
- The caller owns the AWS client. Closing a sandbox does not close the shared AWS client.
- Use a stable client token to reconcile launch retries. A generated token only protects AWS retries within one create call. For durable workflows, call `control.aws().runMicrovm` yourself, persist the returned identifier immediately, then wait/attach. A failed readiness wait does not terminate the launched VM.
- Configure AWS API timeouts on the client: an in-flight AWS call can exceed the polling deadline. Interrupted waits propagate interruption. Set the guest HTTP timeout above the process timeout.
- Guest errors expose status, not potentially sensitive response bodies. Invalid base64 fails explicitly. TLS certificate verification stays enabled; inject an HTTP client into `AgentClient` for custom trust stores. AWS-backed Sandbox endpoints require HTTPS.
- `stop()` and `close()` **terminate**, matching Python SDK semantics. The Python CLI's `stop` command instead suspends.
- Tests use local HTTP servers and fake AWS credentials. They do not create AWS resources. A real cloud lifecycle smoke test remains necessary before production use.
