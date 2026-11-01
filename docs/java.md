# VMNest Java SDK

Java 21+ SDK for AWS Lambda MicroVMs: lifecycle, execution, files, monitoring, images, networking, and provisioning.

## Install and download

From the repository root:

```sh
./gradlew test build publishToMavenLocal
```

On Windows use `gradlew.bat`. In your Gradle application:

```groovy
repositories {
    mavenLocal()
    mavenCentral()
}
dependencies {
    implementation 'io.github.jnh12:vmnest-java:0.1.0-SNAPSHOT'
}
```

For a prebuilt download, open a successful [Java SDK workflow run](https://github.com/jnh12/agent-microvm/actions/workflows/java.yml) and download its `vmnest-java` artifact (GitHub sign-in required). It includes the library, sources, Javadoc, and a Maven repository ZIP with dependency metadata. Extract that ZIP and replace `mavenLocal()` with:

```groovy
maven { url = uri('/absolute/path/to/repository') }
```

Keep `mavenCentral()` for dependencies. Maven consumers can use the extracted directory as a `file:///.../repository` repository with the same group, artifact, and version.

`./gradlew sdkDistribution` builds the ZIP in `build/distributions`. CI downloads expire after 30 days. The SDK is not published to Maven Central. Public publishing requires a verified Central namespace, signing keys, and publishing credentials; after publication consumers can use `mavenCentral()` and the release version directly.

## Usage

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
    } // terminates VM
}
```

## Lifecycle API

| Operation | Java API |
|---|---|
| Sandbox create, attach, identity, endpoint, state | `Sandbox.create`, `attach`, `name`, `microvmId`, `endpoint`, `state` |
| exec (args/cwd/env/timeout), read_file, write_file | `exec`, `readFile`, `writeFile`; binary byte arrays |
| refresh, inspect | `refresh`, `inspect` (typed AWS response) |
| suspend, start/resume, stop/terminate/kill | Lifecycle methods |
| Scoped lifecycle | `AutoCloseable`; close terminates |
| AgentClient health, token refresh, port routing | `AgentClient`; refresh once on 401, including health |
| ControlPlane run/get/list/suspend/resume/terminate | `control.aws()` typed AWS operations and paginators |
| auth tokens, create/list/managed-list/delete images | `control.aws()` typed AWS operations |
| wait_until_running | `waitUntilRunning` with timeout, interval, terminal-state checks |
| IdlePolicy / creation options | AWS `RunMicrovmRequest` and `IdlePolicy` builders |
| ExecResult | `ExecResult` with byte arrays, UTF-8 accessors, success |

The AWS client additionally exposes image version/build/tag APIs, idempotency tokens, and shell tokens. Use its standard credential/region provider chain; the library never stores credentials. Image and role default to `AGENT_SANDBOX_IMAGE_ARN` and `AGENT_SANDBOX_EXECUTION_ROLE_ARN`. The default idle policy is 900/300 seconds with auto-resume. CPU/memory are image settings, not launch-time resizing options.

## Operations

The SDK exposes the 35 operations available through the MCP tools. `CoverageTest` reads the Python tool source and verifies that the mapping stays complete when upstream tools change. This checks surface coverage; separate behavior tests and the opt-in live test validate implementations.

| Operation | Java API |
|---|---|
| `runtime_check`, `runtime_install` | `RuntimeConfig.check`, `installationHelp` |
| `infra_outputs`, `infra_list` | `Infrastructure.outputs`, `list` |
| `sandbox_run`, `sandbox_create`, `sandbox_list`, `sandbox_status`, `sandbox_remove` | `SandboxRegistry.run`, `create`, `list`, `statusAll`, `remove` |
| `sandbox_start`, `sandbox_stop`, `sandbox_inspect`, `sandbox_wait` | `Sandbox.start`, **`suspend`**, `inspect`, `waitFor` |
| `sandbox_exec`, `sandbox_shell` | `Sandbox.exec`, `shell` |
| `sandbox_fs_read/write/list/mkdir/remove/copy/rename/stat/exists` | `SandboxFiles` methods |
| `sandbox_fs_copy_from_host`, `sandbox_fs_copy_to_host` | `SandboxFiles.copyFromHost`, `copyToHost` |
| `sandbox_logs_read`, `sandbox_logs_stream` | `Observability.readLogs`, `pollLogs` |
| `sandbox_metrics`, `sandbox_metrics_all`, `sandbox_metrics_stream` | `Observability.metrics`, `metricsAll`, `sampleMetrics` |
| `image_list`, `image_inspect`, `image_remove`, `image_prune` | `Images.list/managed`, `inspect`, `remove`, `prune` |
| CLI image build, guest-source packaging | `Images.archive`, `extractBundledGuest`, `build`, `waitActive` |
| CLI port forwarding | `Sandbox.forward` / `PortForward` |
| Provisioner plan/up/rebuild/refresh/outputs/destroy | `Infrastructure` with typed `Spec` and an ownership journal |
| VPC/subnet/security-group/operator-role helpers | `Networking` plus native AWS clients |

This package provides a Java library; the CLI and MCP server are distributed with the Python package. Configuration uses Java records and AWS request builders, rather than YAML. Java's registry/journal formats are separate from Python's state files. MCP response envelopes and text truncation are presentation-layer concerns; Java returns typed objects/raw bytes. Infrastructure and network helpers cover the same resource operations but explicitly require ownership decisions when using existing resources.

## Files, logs, metrics, and previews

```java
var files = new SandboxFiles(sandbox.agent(), "/work", List.of(Path.of("./transfer")),
    Duration.ofSeconds(30)); // host allowlist roots must already exist
files.mkdir("project");
files.write("project/hello.txt", "hello".getBytes(StandardCharsets.UTF_8));
files.copyToHost("project/hello.txt", Path.of("./transfer/hello.txt"));

try (var preview = sandbox.forward(8000, 0)) {
    System.out.println("http://localhost:" + preview.localPort());
    // Keep this handle open while using the preview.
}

try (var cw = CloudWatchClient.create(); var logs = CloudWatchLogsClient.create()) {
    var monitoring = new Observability(cw, logs);
    var metrics = monitoring.metrics(sandbox.microvmId(), Duration.ofMinutes(30));
    var events = monitoring.readLogs(actualImageLogGroup, 100, Instant.now().minusSeconds(3600), null);
}
```

Use your configured image log group; it may differ from `/aws/lambda-microvms/<vm-id>`. Missing metric data is `OptionalDouble.empty()`, not zero. Log reads paginate and retain the requested tail after filtering. Use the cursor returned from `pollLogs` for subsequent calls. Filesystem helpers run argument-separated Python snippets **inside the existing guest**, not on the Java host. Recreate guest-bound `SandboxFiles` handles after resume because endpoints/tokens can change.

## Persistent computers and provisioning

`SandboxRegistry` holds an OS file lock for its lifetime. Close it before reopening the same journal. It records a launched VM before readiness polling, so a timeout remains recoverable by name. A failed journal write attempts termination. Use one registry per AWS region/client and coordinate operations on each sandbox.

`Infrastructure.Spec` accepts a reused role ARN **or** a native `CreateRoleRequest`, an explicitly reused/new bucket, a guest directory and `CreateMicrovmImageRequest`, and an optional reused/new connector. Specify base-image settings and lifecycle hooks on the image request. `Networking.operatorRole` creates a suitable Lambda trust request; `grantOperatorPermissions` grants ENI management permissions when provisioning a network operator role. Security groups and operator roles created separately through `Networking` remain caller-owned and must be explicitly deleted through its cleanup methods/native IAM APIs.

The infrastructure journal records ownership incrementally. `destroy(true)` deletes only managed image/connector/bucket/role resources, including bucket versions; use dedicated buckets. Reused resources are preserved. Keep a separate journal per stack. `Infrastructure.list` discovers journals in a directory. Provisioning is not transactional; keep the journal after failures and inspect/reconcile resources before retrying. Client regions must match the spec. IAM propagation can require a retry after creating a new role. No provisioning/destroy method is called by the live test.

`Images.extractBundledGuest` unpacks the repository's guest source from the JAR into a new directory; all code retains the repository license. Image archives reject symlinks and omit `.git`, `.venv`, and Python caches. `build` streams the archive through a temporary file and S3; it requires build-bucket permissions. `waitActive(arn, expectedVersion, timeout)` avoids mistaking an old active version for a completed rebuild.

## Validation

```sh
./gradlew test build publish  # publish only to build/repository, not a remote registry
```

Tests cover lifecycle, signing/serialization, token refresh limits, filesystem transport, host policy, log pagination, metrics, registry locking/persistence, archive contents, provisioning ownership/partial failure, and forwarding. The live test is skipped unless explicitly enabled:

```sh
VMNEST_JAVA_LIVE=1 AWS_REGION=us-east-1 \
AGENT_SANDBOX_IMAGE_ARN=... AGENT_SANDBOX_EXECUTION_ROLE_ARN=... \
VMNEST_JAVA_LOG_GROUP=... ./gradlew test --tests LiveTest --rerun-tasks
```

This creates one disposable VM with a ten-minute maximum lifetime. It tests execution, filesystem operations and binary host transfers, application forwarding, suspend/resume with preserved data, and CloudWatch access. It always attempts termination and verifies completion. `build/live-vm-id.txt` remains as a recovery record if cleanup fails. AWS usage charges apply. CloudWatch log reads are exercised when `VMNEST_JAVA_LOG_GROUP` is supplied. IAM/S3/network/image-build mutations are covered by local tests; the live test does not provision infrastructure.

## Operational behavior

- Calls block; use Java virtual threads when integrating with concurrent applications. Sandbox handles are not thread-safe; coordinate lifecycle changes per VM.
- The caller owns AWS clients. Closing a sandbox terminates the VM and closes its guest transport, but not the shared AWS client. `AgentClient.close` closes only internally created HTTP clients; injected clients remain caller-owned.
- Use a stable client token to reconcile launch retries. A generated token only protects AWS retries within one create call. For durable workflows, call `control.aws().runMicrovm` yourself, persist the returned identifier immediately, then wait/attach. A failed readiness wait does not terminate the launched VM.
- Configure AWS API timeouts on the client: an in-flight AWS call can exceed the polling deadline. Interrupted waits propagate interruption. Set the guest HTTP timeout above the process timeout.
- Guest errors expose status, not potentially sensitive response bodies. Invalid base64 fails explicitly. TLS certificate verification stays enabled; inject an HTTP client into `AgentClient` for custom trust stores. AWS-backed Sandbox endpoints require HTTPS.
- `stop()` and `close()` **terminate**, matching Python SDK semantics. The Python CLI's `stop` command instead suspends.
- Default tests use local HTTP servers and fake AWS credentials. Only the explicitly enabled live test creates a VM. The guest API does not provide PTY/streamed execution, SSH/SFTP, or content snapshots.
- Coordinate image pruning against concurrent launches. Host allowlists resolve symlinks, but callers must prevent concurrent replacement of host directories during transfers. Port forwarding binds loopback, supports ordinary HTTP (including h2c downgrade), rejects WebSocket upgrades, and limits request bodies to 16 MiB.
