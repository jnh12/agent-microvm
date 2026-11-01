import static org.junit.jupiter.api.Assertions.*;

import io.github.jnh12.vmnest.*;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import software.amazon.awssdk.services.iam.*;
import software.amazon.awssdk.services.iam.model.*;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;
import software.amazon.awssdk.services.lambdamicrovms.paginators.*;
import software.amazon.awssdk.services.s3.*;
import software.amazon.awssdk.services.s3.model.*;
import software.amazon.awssdk.services.s3.paginators.*;

class InfrastructureTest {
  @TempDir Path dir;

  @Test
  void reusedResourcesAreNeverDestroyed() throws Exception {
    var calls = new ArrayList<String>();
    var s3 =
        ExtendedTest.fake(
            S3Client.class,
            (p, m, a) -> {
              calls.add(m.getName());
              if (!m.getName().equals("headBucket"))
                throw new AssertionError("Unexpected S3 mutation");
              return HeadBucketResponse.builder().build();
            });
    var vm =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) ->
                switch (m.getName()) {
                  case "listMicrovmImagesPaginator" ->
                      new ListMicrovmImagesIterable(
                          (LambdaMicrovmsClient) p, (ListMicrovmImagesRequest) a[0]);
                  case "listMicrovmImages" ->
                      ListMicrovmImagesResponse.builder()
                          .items(
                              MicrovmImageSummary.builder()
                                  .name("existing")
                                  .imageArn("existing-arn")
                                  .latestActiveImageVersion("1")
                                  .build())
                          .build();
                  case "getMicrovmImage" ->
                      GetMicrovmImageResponse.builder()
                          .imageArn("existing-arn")
                          .latestActiveImageVersion("1")
                          .build();
                  default -> throw new AssertionError("Unexpected mutation: " + m.getName());
                });
    var infra = new Infrastructure(null, s3, vm, null, dir.resolve("state.json"));
    var spec =
        new Infrastructure.Spec(
            "us-east-1",
            "existing-role",
            null,
            List.of(),
            "existing-bucket",
            true,
            dir,
            CreateMicrovmImageRequest.builder().name("existing").build(),
            null,
            null);
    assertEquals("reuse", infra.plan(spec).get("role"));
    assertEquals("1", infra.up(spec, false, Duration.ofSeconds(1)).get("image_version"));
    assertEquals("existing-arn", infra.refresh().get("image_arn"));
    assertThrows(IllegalArgumentException.class, () -> infra.destroy(false));
    infra.destroy(true);
    assertEquals(List.of("headBucket"), calls);
  }

  @Test
  void partialFailureRetainsOwnershipAndDestroyOnlyOwnedResources() throws Exception {
    var calls = new ArrayList<String>();
    var iam =
        ExtendedTest.fake(
            IamClient.class,
            (p, m, a) -> {
              calls.add(m.getName());
              return m.getName().equals("createRole")
                  ? CreateRoleResponse.builder()
                      .role(Role.builder().arn("owned-role-arn").roleName("owned-role").build())
                      .build()
                  : null;
            });
    var s3 =
        ExtendedTest.fake(
            S3Client.class,
            (p, m, a) -> {
              calls.add(m.getName());
              return switch (m.getName()) {
                case "createBucket" -> CreateBucketResponse.builder().build();
                case "putPublicAccessBlock" -> throw new IllegalStateException("injected failure");
                case "listObjectVersionsPaginator" ->
                    new ListObjectVersionsIterable(
                        (S3Client) p,
                        ((java.util.function.Consumer<ListObjectVersionsRequest.Builder>) a[0])
                                == null
                            ? null
                            : ListObjectVersionsRequest.builder().bucket("owned-bucket").build());
                case "listObjectVersions" -> ListObjectVersionsResponse.builder().build();
                case "deleteBucket" -> DeleteBucketResponse.builder().build();
                default -> throw new AssertionError(m.getName());
              };
            });
    Path state = dir.resolve("state.json");
    var infra = new Infrastructure(iam, s3, null, null, state);
    var spec =
        new Infrastructure.Spec(
            "us-east-1",
            null,
            Networking.operatorRole("owned-role"),
            List.of(),
            "owned-bucket",
            false,
            dir,
            CreateMicrovmImageRequest.builder().name("new").build(),
            null,
            null);
    assertThrows(IllegalStateException.class, () -> infra.up(spec, false, Duration.ofSeconds(1)));
    String saved = Files.readString(state);
    assertTrue(saved.contains("owned-role-arn"));
    assertTrue(saved.contains("owned-bucket"));
    infra.destroy(true);
    assertTrue(calls.indexOf("deleteBucket") < calls.indexOf("deleteRole"));
    assertFalse(Files.exists(state));
  }

  @Test
  void nativeConfigurationPreservesLaunchAndNetworkOptions() throws Exception {
    var config =
        RuntimeConfig.fromEnvironment(
            Map.of(
                "AGENT_SANDBOX_IMAGE_ARN",
                "image",
                "AGENT_SANDBOX_EXECUTION_ROLE_ARN",
                "role",
                "AGENT_SANDBOX_EGRESS_CONNECTOR",
                "egress"));
    var request = config.request("stable");
    assertEquals("stable", request.clientToken());
    assertEquals(List.of("egress"), request.egressNetworkConnectors());
    assertEquals(900, request.idlePolicy().maxIdleDurationSeconds());
    var connector = Networking.connector("test", List.of("subnet"), List.of("sg"), "role");
    assertEquals(List.of("subnet"), connector.configuration().vpcEgressConfiguration().subnetIds());
  }
}
