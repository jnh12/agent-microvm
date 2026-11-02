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
    // Journals from earlier PR builds remain readable after removing unused policy bookkeeping.
    var json = new com.fasterxml.jackson.databind.ObjectMapper();
    var journal = dir.resolve("state.json");
    var oldState = (com.fasterxml.jackson.databind.node.ObjectNode) json.readTree(journal.toFile());
    oldState.putArray("attachedPolicies").add("unused-policy");
    json.writeValue(journal.toFile(), oldState);
    assertEquals("existing-arn", infra.refresh().get("image_arn"));
    assertFalse(json.readTree(journal.toFile()).has("attachedPolicies"));
    assertThrows(IllegalArgumentException.class, () -> infra.destroy(false));
    infra.destroy(true);
    assertEquals(List.of("headBucket"), calls);
  }

  @org.junit.jupiter.params.ParameterizedTest
  @org.junit.jupiter.params.provider.ValueSource(ints = {0, 1, 2})
  void partialFailureRetainsOwnershipAndDestroyOnlyOwnedResources(int policyState)
      throws Exception {
    var calls = new ArrayList<String>();
    var iam =
        ExtendedTest.fake(
            IamClient.class,
            (p, m, a) -> {
              calls.add(m.getName());
              if (m.getName().equals("deleteRolePolicy")) {
                if (policyState == 1)
                  throw NoSuchEntityException.builder()
                      .message("Policy disappeared after listing")
                      .build();
                throw IamException.builder().statusCode(403).message("Access denied").build();
              }
              if (m.getName().equals("listRolePoliciesPaginator"))
                return new software.amazon.awssdk.services.iam.paginators.ListRolePoliciesIterable(
                    (IamClient) p,
                    ListRolePoliciesRequest.builder().roleName("owned-role").build());
              if (m.getName().equals("listRolePolicies"))
                return ListRolePoliciesResponse.builder()
                    .policyNames(policyState == 0 ? List.of() : List.of("vmnest-build"))
                    .build();
              if (m.getName().equals("listAttachedRolePoliciesPaginator"))
                return new software.amazon.awssdk.services.iam.paginators
                    .ListAttachedRolePoliciesIterable(
                    (IamClient) p,
                    ListAttachedRolePoliciesRequest.builder().roleName("owned-role").build());
              if (m.getName().equals("listAttachedRolePolicies"))
                return ListAttachedRolePoliciesResponse.builder().build();
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
                case "headBucket" -> throw S3Exception.builder().statusCode(404).build();
                case "createBucket" -> CreateBucketResponse.builder().build();
                case "putPublicAccessBlock" -> throw new IllegalStateException("injected failure");
                case "listObjectVersionsPaginator" ->
                    new ListObjectVersionsIterable(
                        (S3Client) p,
                        ListObjectVersionsRequest.builder().bucket("owned-bucket").build());
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
    if (policyState == 2) {
      assertThrows(IamException.class, () -> infra.destroy(true));
      assertFalse(calls.contains("deleteRole"));
      assertTrue(Files.exists(state));
      return;
    }
    infra.destroy(true);
    assertEquals(policyState == 1, calls.contains("deleteRolePolicy"));
    assertTrue(calls.indexOf("deleteBucket") < calls.indexOf("deleteRole"));
    assertFalse(Files.exists(state));
  }

  @org.junit.jupiter.params.ParameterizedTest
  @org.junit.jupiter.params.provider.ValueSource(ints = {200, 403, 301, 500, -1})
  void existingOrUnknownBucketIsNeverCreatedOrOwned(int status) throws Exception {
    var calls = new ArrayList<String>();
    var s3 =
        ExtendedTest.fake(
            S3Client.class,
            (p, m, a) -> {
              calls.add(m.getName());
              if (m.getName().equals("headBucket")) {
                if (status == 200) return HeadBucketResponse.builder().build();
                if (status == -1)
                  throw software.amazon.awssdk.core.exception.SdkClientException.create(
                      "network failure");
                throw S3Exception.builder().statusCode(status).build();
              }
              // Reproduce us-east-1: creation WOULD succeed even though the bucket already exists.
              if (m.getName().equals("createBucket")) return CreateBucketResponse.builder().build();
              throw new AssertionError("Existing bucket must never be mutated: " + m.getName());
            });
    Path journal = dir.resolve("protected.json");
    var infra = new Infrastructure(null, s3, null, null, journal);
    var spec =
        new Infrastructure.Spec(
            "us-east-1",
            "reused-role",
            null,
            List.of(),
            "unrelated-data",
            false,
            dir,
            CreateMicrovmImageRequest.builder().name("image").build(),
            null,
            null);
    assertThrows(RuntimeException.class, () -> infra.up(spec, false, Duration.ofSeconds(1)));
    assertFalse(Files.exists(journal));
    infra.destroy(true);
    assertEquals(List.of("headBucket"), calls);
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
