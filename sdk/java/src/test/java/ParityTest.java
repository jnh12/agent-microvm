import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.*;
import io.github.jnh12.vmnest.*;
import java.net.*;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.atomic.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;
import software.amazon.awssdk.services.lambdamicrovms.model.AccessDeniedException;

class ParityTest {
  @TempDir Path dir;

  @Test
  void runtimeDiagnosticsAndStoredRegions() throws Exception {
    try (var aws =
        LambdaMicrovmsClient.builder()
            .region(software.amazon.awssdk.regions.Region.US_EAST_1)
            .credentialsProvider(
                software.amazon.awssdk.auth.credentials.StaticCredentialsProvider.create(
                    software.amazon.awssdk.auth.credentials.AwsBasicCredentials.create(
                        "test", "test")))
            .build()) {
      var diagnostics =
          new RuntimeConfig("image", "role", "us-east-1", "/work", null)
              .diagnostics(new ControlPlane(aws));
      assertTrue(diagnostics.ready());
      assertTrue(diagnostics.awsCredentials());
    }
    Path file = dir.resolve("regions.json");
    Files.writeString(
        file,
        "{\"sandboxes\":{\"west\":{\"name\":\"west\",\"microvm_id\":\"vm-west\",\"region\":\"us-west-2\"}}}");
    var base =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> {
              throw new AssertionError("wrong region");
            });
    var west =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> GetMicrovmResponse.builder().state("RUNNING").build());
    try (var registry =
        new SandboxRegistry(
            file,
            new ControlPlane(base),
            region -> {
              assertEquals("us-west-2", region);
              return new ControlPlane(west);
            })) {
      assertEquals("running", registry.statusAll().get("west"));
    }
    Path empty = dir.resolve("empty.yaml");
    Files.writeString(empty, "---\n");
    assertThrows(java.io.IOException.class, () -> InfraConfig.loadAll(List.of(empty), null));
  }

  @Test
  void transientLaunchRetriesUseOneTokenButPermissionFailuresDoNotRetry() throws Exception {
    var calls = new AtomicInteger();
    var tokens = new HashSet<String>();
    var client =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> {
              var request = (RunMicrovmRequest) a[0];
              tokens.add(request.clientToken());
              if (calls.incrementAndGet() == 1)
                throw ResourceNotFoundException.builder().message("No active version").build();
              return RunMicrovmResponse.builder().microvmId("vm").build();
            });
    assertEquals(
        "vm",
        new ControlPlane(client)
            .runMicrovm(
                RunMicrovmRequest.builder().imageIdentifier("image").build(), Duration.ofMillis(10))
            .microvmId());
    assertEquals(2, calls.get());
    assertEquals(1, tokens.size());
    assertFalse(tokens.contains(null));
    calls.set(0);
    var denied =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> {
              calls.incrementAndGet();
              throw AccessDeniedException.builder().message("denied").build();
            });
    assertThrows(
        AccessDeniedException.class,
        () -> new ControlPlane(denied).runMicrovm(RunMicrovmRequest.builder().build()));
    assertEquals(1, calls.get());
  }

  @Test
  void attachmentUsesFallbackAndStateMappingIgnoresCase() {
    var aws =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> GetMicrovmResponse.builder().microvmId("vm").state("SUSPENDED").build());
    var vm =
        Sandbox.attach(
            "vm", "vm", new ControlPlane(aws), 8080, Duration.ofSeconds(2), "fallback.example");
    assertEquals("fallback.example", vm.endpoint());
    assertEquals("running", Sandbox.normalizedState("running"));
    assertEquals("pending", Sandbox.normalizedState("resuming"));
    assertEquals("unknown", Sandbox.normalizedState(null));
  }

  @Test
  void unavailableGuestHealthIsFalse() throws Exception {
    int port;
    try (var socket = new java.net.ServerSocket(0)) {
      port = socket.getLocalPort();
    }
    try (var agent =
        new AgentClient(
            URI.create("http://localhost:" + port), () -> "token", 8080, Duration.ofMillis(200))) {
      assertFalse(agent.health());
    }
  }

  @Test
  void yamlSupportsRelativeGuestsMultiDocumentsDirectoriesAndDefaults() throws Exception {
    Path guest = Files.createDirectory(dir.resolve("source"));
    Files.writeString(guest.resolve("Dockerfile"), "FROM scratch");
    Path file = dir.resolve("sandbox.yaml");
    Files.writeString(
        file,
        "project: example\n"
            + "stack: dev\n"
            + "image:\n"
            + "  guest_dir: source\n"
            + "role:\n"
            + "  arn: existing\n"
            + "bucket:\n"
            + "  name: reused\n"
            + "---\n"
            + "stack: production\n"
            + "image:\n"
            + "  guest_dir: source\n");
    var configs = InfraConfig.loadAll(List.of(dir), "test");
    assertEquals(2, configs.size());
    assertEquals("test", configs.get(1).stack());
    var spec = configs.getFirst().spec(Map.of());
    assertEquals(guest, spec.guestSource());
    assertTrue(spec.reuseBucket());
    assertEquals("reused", spec.bucket());
    assertEquals("existing", spec.existingRoleArn());
    assertEquals("us-east-1", spec.region());
    var hooks = Images.defaultHooks();
    assertEquals(Ports.hook(), hooks.port());
    assertEquals("ENABLED", hooks.microvmHooks().runAsString());
    assertEquals(60, hooks.microvmImageHooks().readyTimeoutInSeconds());
    assertThrows(
        java.io.IOException.class,
        () -> Images.resolveGuestDirectory(dir.resolve("missing-custom"), false));
    assertTrue(Images.isGuestDirectory(Images.resolveGuestDirectory(dir.resolve("guest"), true)));
    Files.writeString(file, "- not-a-mapping\n");
    assertThrows(java.io.IOException.class, () -> InfraConfig.loadAll(List.of(file), null));
  }

  @Test
  void pythonSandboxRecordsAreReadAndWritesRemainPythonCompatible() throws Exception {
    Path file = dir.resolve("state.json");
    Files.writeString(
        file,
        "{\"sandboxes\":{\"existing\":{\"name\":\"existing\",\"microvm_id\":\"vm-1\",\"endpoint\":\"example.com\",\"image_arn\":\"image\",\"region\":null}}}");
    var aws =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) ->
                GetMicrovmResponse.builder()
                    .microvmId("vm-2")
                    .endpoint("second.example")
                    .imageArn("image2")
                    .state("RUNNING")
                    .build());
    try (var registry = new SandboxRegistry(file, new ControlPlane(aws))) {
      assertEquals("vm-1", registry.list().getFirst().microvmId());
      registry.attach("second", "vm-2", 8081);
      var tree = new ObjectMapper().readTree(file.toFile());
      assertEquals("vm-2", tree.at("/sandboxes/second/microvm_id").asText());
      var fields = new HashSet<String>();
      tree.at("/sandboxes/second").fieldNames().forEachRemaining(fields::add);
      assertEquals(Set.of("name", "microvm_id", "endpoint", "image_arn", "region"), fields);
      assertEquals(
          8081,
          registry.list().stream()
              .filter(e -> e.name().equals("second"))
              .findFirst()
              .orElseThrow()
              .agentPort());
    }
  }

  @Test
  void sharedInfrastructureStateRefreshesMissingImageAndPreservesOtherStacks() throws Exception {
    Path file = dir.resolve("infra.json");
    Files.writeString(
        file,
        "{\"stacks\":{\"p/dev\":{\"resources\":{\"image\":{\"id\":\"missing\",\"kind\":\"microvm-image\",\"managed\":true}},\"outputs\":{\"image_arn\":\"missing\",\"image_version\":\"1\"}},\"other/dev\":{\"resources\":{},\"outputs\":{\"keep\":\"yes\"}}}}");
    var config = new InfraConfig(new ObjectMapper().readTree("{\"project\":\"p\"}"), dir);
    var aws =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> {
              throw ResourceNotFoundException.builder().build();
            });
    var infra = Infrastructure.forConfig(config, null, null, aws, null, null, file);
    assertEquals("missing", infra.outputs().get("image_arn"));
    assertFalse(infra.refresh().containsKey("image_arn"));
    infra.destroy(true);
    var tree = new ObjectMapper().readTree(file.toFile());
    assertFalse(tree.path("stacks").has("p/dev"));
    assertEquals(
        "yes", tree.path("stacks").path("other/dev").path("outputs").path("keep").asText());
  }

  @Test
  void metricFailuresAreIsolatedAndSamplesHaveTimestamps() throws Exception {
    var client =
        ExtendedTest.fake(
            software.amazon.awssdk.services.cloudwatch.CloudWatchClient.class,
            (p, m, a) -> {
              var builder =
                  software.amazon.awssdk.services.cloudwatch.model.GetMetricStatisticsRequest
                      .builder();
              ((java.util.function.Consumer<
                          software.amazon.awssdk.services.cloudwatch.model
                              .GetMetricStatisticsRequest.Builder>)
                      a[0])
                  .accept(builder);
              if (builder.build().dimensions().getFirst().value().equals("bad"))
                throw new IllegalStateException("unavailable");
              return software.amazon.awssdk.services.cloudwatch.model.GetMetricStatisticsResponse
                  .builder()
                  .build();
            });
    var monitoring = new Observability(client, null);
    var all = monitoring.metricsAll(List.of("bad", "good"), Duration.ofMinutes(5));
    assertEquals("unavailable", all.get("bad").error());
    assertNull(all.get("good").error());
    var samples = monitoring.sampleMetrics("good", 0, Duration.ZERO);
    assertEquals(1, samples.size());
    assertEquals(0, samples.getFirst().sample());
    assertNotNull(samples.getFirst().at());
  }
}
