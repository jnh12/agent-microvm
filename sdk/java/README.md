# VMNest Java SDK

Java 21+ SDK for AWS Lambda MicroVMs: lifecycle, execution, files, monitoring, images, networking, and provisioning.

## Install and download

Add the released SDK from Maven Central to your Gradle application:

```groovy
repositories { mavenCentral() }
dependencies {
    implementation 'io.github.jnh12:vmnest-java:0.3.0'
}
```

For Maven:

```xml
<dependency>
  <groupId>io.github.jnh12</groupId>
  <artifactId>vmnest-java</artifactId>
  <version>0.3.0</version>
</dependency>
```

### Building a development version

From a repository checkout:

```sh
cd sdk/java
./gradlew test build publishToMavenLocal
```

On Windows use `gradlew.bat`. In your Gradle application:

```groovy
repositories {
    mavenLocal()
    mavenCentral()
}
dependencies {
    implementation 'io.github.jnh12:vmnest-java:0.3.0-SNAPSHOT'
}
```

For a prebuilt download, open a successful [Java SDK workflow run](https://github.com/jnh12/agent-microvm/actions/workflows/java.yml) and download its `vmnest-java` artifact (GitHub sign-in required). It includes the library, sources, Javadoc, and a Maven repository ZIP with dependency metadata. Extract that ZIP and replace `mavenLocal()` with:

```groovy
maven { url = uri('/absolute/path/to/repository') }
```

Keep `mavenCentral()` for dependencies. Maven consumers can use the extracted directory as a `file:///.../repository` repository with the same group, artifact, and version.

`./gradlew sdkDistribution` builds the ZIP in `build/distributions`. CI downloads expire after 30 days.

### Publishing a release

The **Release Java SDK** workflow accepts a release version such as `0.1.0` and runs the Java tests, cross-language contract checks, distribution build, and separate consumer compilation before signing and uploading. Leave **publish** unchecked to validate an upload for manual publication in the Central Portal; check it to publish after validation. Once published, a version cannot be replaced.

The repository secrets are `MAVEN_CENTRAL_USERNAME` and `MAVEN_CENTRAL_PASSWORD` (the Portal user-token pair), `MAVEN_SIGNING_KEY` (ASCII-armored private GPG key), and optionally `MAVEN_SIGNING_PASSWORD` for a protected key. The corresponding public key must be available on a supported public keyserver, and the Portal account must own `io.github.jnh12`. Never commit these credentials.

For a local release, supply the credentials through `ORG_GRADLE_PROJECT_mavenCentralUsername`, `ORG_GRADLE_PROJECT_mavenCentralPassword`, `ORG_GRADLE_PROJECT_signingInMemoryKey`, and optionally `ORG_GRADLE_PROJECT_signingInMemoryKeyPassword`. Then run:

```sh
./gradlew --no-daemon publishToMavenCentral -PcentralRelease=true -PsdkVersion=0.1.0
```

Use `publishAndReleaseToMavenCentral` instead to publish automatically after validation. Central tasks are enabled only with `-PcentralRelease=true`; ordinary tests, builds, and local distribution tasks need no credentials. On PowerShell, quote property arguments, for example `'-PsdkVersion=0.1.0'`.

## Hosted use

Use the same SDK with [VMNest Cloud](https://vmnest.cc), without AWS credentials.
Sign in with `vmnest login`, then create an automation credential with
`vmnest token create --name java-app`. Store the output in the secret environment variable
`VMNEST_API_TOKEN`; `VMNEST_API_URL` defaults to `https://vmnest.cc`.
Java uses this explicit SDK credential rather than reading the CLI's protected login file.
New accounts require admin activation. The pilot uses daily runtime allowances;
VMNest billing is disabled. Model-provider usage terms and charges still apply.
Use `cloud.agents()` for profiles, prompt jobs, durable agents and webhooks;
see the [agent examples](../../docs/hosted-agents.md#java-and-mcp).

```java
import io.github.jnh12.vmnest.*;
import java.time.Duration;
import java.util.List;

try (var cloud = HostedClient.fromEnvironment();
     var sandbox = cloud.create("java-example")) {
    var result = sandbox.exec("python", List.of("-c", "print('hello')"),
        null, null, Duration.ofSeconds(30));
    System.out.println(result.stdoutText());
    sandbox.writeFile("/work/hello.txt", "hello".getBytes());
    sandbox.suspend();
    sandbox.resume();
}
```

Existing `Sandbox`, `SandboxFiles` and HTTP/WebSocket forwarding methods work unchanged.
For duration or idle policy, pass an AWS-shaped `RunMicrovmRequest` to
`Sandbox.create` with `cloud.controlPlane()`; the hosted transport validates and converts
it to the public API. AWS roles, networking and image-version overrides are self-hosted options.
`SandboxRegistry(cloud.controlPlane())` resolves account-owned names on the server and does
not read or overwrite your local self-hosted state.

Use `cloud.images()` to list/inspect the preconfigured default image and `cloud.observability()` for logs and
metrics. Hosted metrics are current guest-reported samples, not billing or historical data.
Custom image builds, uploads and removal are available only in self-hosted mode.
Infrastructure provisioning remains an operator/self-hosted operation.

## Usage (self-hosted)

Configure AWS credentials and `AWS_REGION` for the AWS client. Set `AGENT_SANDBOX_IMAGE_ARN` and `AGENT_SANDBOX_EXECUTION_ROLE_ARN` to your image and execution role. The example below runs inside a Java method that declares `throws Exception`.

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

`Sandbox` provides create/attach, identity and state, execution, file reads/writes, refresh/inspect, suspend/resume, and termination. `stop()` and `close()` terminate the VM, matching Python SDK semantics. `ExecResult` contains binary output, UTF-8 accessors, and success status. `AgentClient` handles guest HTTP and retries authentication once on a 401. Use `ControlPlane.waitUntilRunning` for bounded readiness polling.

`control.aws()` exposes typed AWS lifecycle, token, image, version, build, and tag operations and paginators. Use its standard credential/region provider chain; the library never stores credentials. The default idle policy is 900/300 seconds with auto-resume. CPU/memory are image settings, not launch-time resizing options.

## Operations

The SDK exposes the 35 operations available through the MCP tools. `CoverageTest` reads the Python tool source and verifies that the mapping stays complete when upstream tools change. This checks surface coverage; separate behavior tests and the opt-in live test validate implementations.

| Operation | Java API |
|---|---|
| `runtime_check`, `runtime_install` | `RuntimeConfig.diagnostics`, `check`, `installationHelp` |
| `infra_outputs`, `infra_list` | `Infrastructure.outputs`, `listStacks` |
| `sandbox_run`, `sandbox_create`, `sandbox_list`, `sandbox_status`, `sandbox_remove` | `SandboxRegistry.run`, `create`, `list`, `statusAll`, `remove` |
| `sandbox_start`, `sandbox_stop`, `sandbox_inspect`, `sandbox_wait` | `Sandbox.start`, **`suspend`**, `inspect`, `waitFor` |
| `sandbox_exec`, `sandbox_shell` | `Sandbox.exec`, `shell` |
| `sandbox_fs_read/write/list/mkdir/remove/copy/rename/stat/exists` | `SandboxFiles` methods |
| `sandbox_fs_copy_from_host`, `sandbox_fs_copy_to_host` | `SandboxFiles.copyFromHost`, `copyToHost` |
| `sandbox_logs_read`, `sandbox_logs_stream` | `Observability.readLogs`, `pollLogs` |
| `sandbox_metrics`, `sandbox_metrics_all`, `sandbox_metrics_stream` | `Observability.metrics`, `metricsAll`, `sampleMetrics` |
| `image_list`, `image_inspect`, `image_remove`, `image_prune` | `Images.list/managed`, `inspect`, `remove`, `prune` |
| CLI image build, guest-source packaging | `Images.archive`, `resolveGuestDirectory`, `extractBundledGuest`, `build`, `waitActive` |
| HTTP / WebSocket forwarding | `Sandbox.forward` / `PortForward` |
| Provisioner plan/up/rebuild/refresh/outputs/destroy | `Infrastructure` with typed `Spec` and an ownership journal |
| VPC/subnet/security-group/operator-role helpers | `Networking` plus native AWS clients |

This package provides a Java library; the CLI and MCP server are distributed with the Python package. `InfraConfig` reads the same `sandbox.yaml` schema, including multiple documents and directories. The default sandbox registry and infrastructure stack store use Python-compatible JSON files; Java records and AWS builders are also available for programmatic configuration. MCP response envelopes and text truncation are presentation-layer concerns; Java returns typed objects/raw bytes. Infrastructure and network helpers cover the same resource operations but explicitly require ownership decisions when using existing resources.

## Files, logs, metrics, and previews

```java
var files = new SandboxFiles(sandbox.agent(), "/work", List.of(Path.of("./transfer")),
    Duration.ofSeconds(30)); // host allowlist roots must already exist
files.mkdir("project");
files.write("project/hello.txt", "hello".getBytes(StandardCharsets.UTF_8));
files.copyToHost("project/hello.txt", Path.of("./transfer/hello.txt"));

try (var preview = sandbox.forward(8000, 0)) {
    System.out.println("http://localhost:" + preview.localPort());
    // HTTP and WebSocket connections use this same local port.
    // Keep this handle open while using the preview.
}

try (var cw = CloudWatchClient.create(); var logs = CloudWatchLogsClient.create()) {
    var monitoring = new Observability(cw, logs);
    var metrics = monitoring.metrics(sandbox.microvmId(), Duration.ofMinutes(30));
    var events = monitoring.readLogs(actualImageLogGroup, 100, Instant.now().minusSeconds(3600), null);
}
```

Use your configured image log group; it may differ from `/aws/lambda-microvms/<vm-id>`. Missing metric data is `OptionalDouble.empty()`, not zero. Log reads paginate and retain the requested tail after filtering. `pollLogs(group, null, duration)` starts a polling session. Pass `batch.cursor()` on subsequent calls and read `batch.events()`. The cursor preserves pagination, timestamps, and delivered event IDs, including across empty responses and long polling pauses. It retains a one-minute overlap behind the latest event for delayed ingestion; events arriving more than a minute behind that watermark require a historical `readLogs` query. Keep cursors scoped to their log group. Raw AWS pagination tokens are not polling checkpoints. Filesystem helpers run argument-separated Python snippets **inside the existing guest**, not on the Java host. Recreate guest-bound `SandboxFiles` handles after resume because endpoints/tokens can change.

## Persistent computers and provisioning

`SandboxRegistry` holds an OS file lock for its lifetime. Close it before reopening the same journal. It records a launched VM before readiness polling, so a timeout remains recoverable by name. A failed journal write attempts termination. Stored regions route operations to the appropriate AWS region. Coordinate operations on each sandbox, and do not write the same state file concurrently from Python and Java. Python does not honor the Java file lock. For custom guest ports, configure the same `AGENT_SANDBOX_AGENT_PORT` in both runtimes; Python preserves Java's per-record port metadata but uses its configured agent port.

`Infrastructure.Spec` accepts a reused role ARN **or** a native `CreateRoleRequest`, an explicitly reused/new bucket, a guest directory and `CreateMicrovmImageRequest`, and an optional reused/new connector. Omitted base images resolve to the newest AWS-managed base image; omitted lifecycle hooks use the same guest defaults as Python. `Networking.operatorRole` creates a suitable Lambda trust request; `grantOperatorPermissions` grants ENI management permissions when provisioning a network operator role. `Infrastructure.NetworkSpec` (or `network.egress` in YAML) provisions and journals the security group, operator role, subnets, and connector together. Helpers called directly through `Networking` remain caller-owned.

To use an existing Python setup and state (AWS clients must use `config.region()`):

```java
var config = InfraConfig.load(Path.of("sandbox.yaml"));
var infrastructure = Infrastructure.forConfig(config, iam, s3, microvms, core, ec2, null);
System.out.println(infrastructure.plan(config));
var outputs = infrastructure.up(config, false, Duration.ofMinutes(15));
// infrastructure.refresh(); infrastructure.destroy(true);
```

`InfraConfig.loadAll(List.of(Path.of("configs")), "dev")` loads multiple YAML files/documents with a stack override. `RuntimeConfig.fromEnvironment()` fills missing image, execution-role and connector settings from the configured stack's outputs.

The infrastructure journal records ownership incrementally. `destroy(true)` deletes only managed image/connector/security-group/operator-role/bucket/execution-role resources, including bucket versions; use dedicated buckets. Reused resources are preserved. Before creating a bucket, provisioning checks its existence: an existing bucket requires `reuseBucket=true`, and access/connection errors stop provisioning. Only a 404 permits creation. Use a unique bucket name and do not create that name concurrently elsewhere; S3 has no atomic create-if-absent guarantee in `us-east-1`. `Infrastructure.forConfig` uses the shared `~/.agent_sandbox/infra-state.json` store, keyed by `project/stack`; `AGENT_SANDBOX_INFRA_STATE` overrides the path. `listStacks` reads all stacks. The lower-level constructor also supports an isolated per-stack journal. Provisioning is not transactional; keep the journal after failures and inspect/reconcile resources before retrying. Client regions must match the spec. Connector creation retries transient role-assumption failures for up to 120 seconds. VM launches retry transient not-found/throttling errors for up to 180 seconds with one idempotency token. No provisioning/destroy method is called by the live test.

`Images.extractBundledGuest` unpacks the repository's guest source from the JAR into a new directory; all code retains the repository license. Image archives reject symlinks and omit `.git`, `.venv`, and Python caches. `build` streams the archive through a temporary file and S3; it requires build-bucket permissions. `waitActive(arn, expectedVersion, timeout)` avoids mistaking an old active version for a completed rebuild.

## Validation

Run from `sdk/java/`:

```sh
./gradlew test build sdkDistribution  # publishes only to build/repository
./gradlew -p tests/consumer compileJava  # checks public API dependencies
```

`python tests/python_contract.py` verifies the fixture against the installed Python package; after Java tests, `python tests/python_contract.py --check-java` verifies Python can read Java-written state. CI runs both checks whenever either SDK changes. The fixture also tracks every public Python `Sandbox`, `AgentClient`, and `ControlPlane` operation so an added Python method requires a reviewed Java mapping.

Tests cover lifecycle, signing/serialization, token refresh limits, filesystem transport, host policy, log pagination, metrics, registry locking/persistence, archive contents, provisioning ownership/partial failure, and forwarding. The live test is skipped unless explicitly enabled:

```sh
VMNEST_JAVA_LIVE=1 AWS_REGION=us-east-1 \
AGENT_SANDBOX_IMAGE_ARN=... AGENT_SANDBOX_EXECUTION_ROLE_ARN=... \
VMNEST_JAVA_LOG_GROUP=... ./gradlew test --tests LiveTest --rerun-tasks
```

This creates one disposable VM with a ten-minute maximum lifetime. It tests execution, filesystem operations and binary host transfers, HTTP and WebSocket application forwarding (text/binary echo and application subprotocols), suspend/resume with preserved data, and CloudWatch access. It always attempts termination and verifies completion. `build/live-vm-id.txt` remains as a recovery record if cleanup fails. AWS usage charges apply. CloudWatch log reads are exercised when `VMNEST_JAVA_LOG_GROUP` is supplied. IAM/S3/network/image-build mutations are covered by local tests; the live test does not provision infrastructure.

## Operational behavior

- Calls block; use Java virtual threads when integrating with concurrent applications. Sandbox handles are not thread-safe; coordinate lifecycle changes per VM.
- The caller owns AWS clients. Closing a sandbox terminates the VM and closes its guest transport, but not the shared AWS client. `AgentClient.close` closes only internally created HTTP clients; injected clients remain caller-owned.
- Use a stable client token to reconcile launch retries. For durable workflows, call `control.aws().runMicrovm` yourself, persist the returned identifier immediately, then wait/attach. A failed readiness wait does not terminate the launched VM.
- Configure AWS API timeouts on the client: an in-flight AWS call can exceed the polling deadline. Interrupted waits propagate interruption. Set the guest HTTP timeout above the process timeout.
- Guest errors expose status, not potentially sensitive response bodies. Invalid base64 fails explicitly. TLS certificate verification stays enabled by default. `sandbox.verifyTls(false)` and the `AgentClient` / `PortForward` boolean overloads match Python's explicit TLS opt-out; prefer injecting a trusted HTTP client into `AgentClient` for private CAs. AWS-backed Sandbox endpoints require HTTPS.
- The Python CLI's `stop` command suspends; the SDK's `stop()` terminates.
- Default tests use local HTTP servers and fake AWS credentials. Only the explicitly enabled live test creates a VM. The guest API does not provide PTY/streamed execution, SSH/SFTP, or content snapshots.
- Coordinate image pruning against concurrent launches. Host allowlists resolve symlinks, but callers must prevent concurrent replacement of host directories during transfers. Port forwarding binds loopback, supports ordinary HTTP (including h2c downgrade) and WebSocket upgrades, and limits ordinary HTTP request bodies to 16 MiB. WebSocket forwarding preserves application subprotocols and frames (including binary data, fragmentation, ping/pong, and close). AWS authentication/routing protocols are added only upstream; TLS certificates and hostnames are verified unless explicitly opted out. Closing the forward disconnects active sessions.
