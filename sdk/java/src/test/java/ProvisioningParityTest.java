import static org.junit.jupiter.api.Assertions.*;

import io.github.jnh12.vmnest.*;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.atomic.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import software.amazon.awssdk.services.ec2.*;
import software.amazon.awssdk.services.ec2.model.*;
import software.amazon.awssdk.services.iam.*;
import software.amazon.awssdk.services.iam.model.*;
import software.amazon.awssdk.services.iam.paginators.*;
import software.amazon.awssdk.services.lambdacore.*;
import software.amazon.awssdk.services.lambdacore.model.*;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;
import software.amazon.awssdk.services.lambdamicrovms.paginators.*;
import software.amazon.awssdk.services.s3.*;
import software.amazon.awssdk.services.s3.model.*;
import software.amazon.awssdk.services.s3.paginators.*;

class ProvisioningParityTest {
  @TempDir Path dir;

  @Test
  void provisionsImageDefaultsAndNetworkThenCleansOwnedResourcesInOrder() throws Exception {
    Files.writeString(dir.resolve("Dockerfile"), "FROM scratch");
    var calls = new ArrayList<String>();
    var imageRequest = new AtomicReference<CreateMicrovmImageRequest>();
    var operatorAttempts = new AtomicInteger();
    var deleted = new AtomicBoolean();
    var iam =
        ExtendedTest.fake(
            IamClient.class,
            (p, m, a) -> {
              calls.add(m.getName());
              return switch (m.getName()) {
                case "createRole" -> {
                  var request = (CreateRoleRequest) a[0];
                  yield CreateRoleResponse.builder()
                      .role(
                          Role.builder()
                              .roleName(request.roleName())
                              .arn("arn:role:" + request.roleName())
                              .build())
                      .build();
                }
                case "putRolePolicy" -> PutRolePolicyResponse.builder().build();
                case "listRolePoliciesPaginator" ->
                    new ListRolePoliciesIterable(
                        (IamClient) p, ListRolePoliciesRequest.builder().roleName("role").build());
                case "listRolePolicies" ->
                    ListRolePoliciesResponse.builder().policyNames("other-inline").build();
                case "listAttachedRolePoliciesPaginator" ->
                    new ListAttachedRolePoliciesIterable(
                        (IamClient) p,
                        ListAttachedRolePoliciesRequest.builder().roleName("role").build());
                case "listAttachedRolePolicies" ->
                    ListAttachedRolePoliciesResponse.builder()
                        .attachedPolicies(
                            AttachedPolicy.builder().policyArn("managed-policy").build())
                        .build();
                case "detachRolePolicy" -> DetachRolePolicyResponse.builder().build();
                case "deleteRolePolicy" -> DeleteRolePolicyResponse.builder().build();
                case "deleteRole" -> DeleteRoleResponse.builder().build();
                default -> throw new AssertionError(m.getName());
              };
            });
    var ec2 =
        new Ec2Client() {
          public String serviceName() {
            return "ec2";
          }

          public void close() {}

          public CreateSecurityGroupResponse createSecurityGroup(
              CreateSecurityGroupRequest request) {
            calls.add("createSecurityGroup");
            return CreateSecurityGroupResponse.builder().groupId("sg-test").build();
          }

          public DescribeSubnetsResponse describeSubnets(DescribeSubnetsRequest request) {
            calls.add("describeSubnets");
            return DescribeSubnetsResponse.builder()
                .subnets(Subnet.builder().subnetId("subnet-123").build())
                .build();
          }

          public DeleteSecurityGroupResponse deleteSecurityGroup(
              DeleteSecurityGroupRequest request) {
            calls.add("deleteSecurityGroup");
            return DeleteSecurityGroupResponse.builder().build();
          }
        };
    var s3 =
        ExtendedTest.fake(
            S3Client.class,
            (p, m, a) -> {
              calls.add(m.getName());
              return switch (m.getName()) {
                case "headBucket" -> throw S3Exception.builder().statusCode(404).build();
                case "createBucket" -> CreateBucketResponse.builder().build();
                case "putPublicAccessBlock" -> PutPublicAccessBlockResponse.builder().build();
                case "putBucketVersioning" -> PutBucketVersioningResponse.builder().build();
                case "putBucketEncryption" -> PutBucketEncryptionResponse.builder().build();
                case "putObject" -> PutObjectResponse.builder().build();
                case "listObjectVersionsPaginator" ->
                    new ListObjectVersionsIterable(
                        (S3Client) p, ListObjectVersionsRequest.builder().bucket("bucket").build());
                case "listObjectVersions" -> ListObjectVersionsResponse.builder().build();
                case "deleteBucket" -> DeleteBucketResponse.builder().build();
                default -> throw new AssertionError(m.getName());
              };
            });
    var core =
        ExtendedTest.fake(
            LambdaCoreClient.class,
            (p, m, a) -> {
              calls.add(m.getName());
              return switch (m.getName()) {
                case "createNetworkConnector" -> {
                  if (operatorAttempts.incrementAndGet() == 1)
                    throw LambdaCoreException.builder()
                        .message("Unable to assume operator role")
                        .build();
                  yield CreateNetworkConnectorResponse.builder().arn("connector").build();
                }
                case "getNetworkConnector" -> {
                  if (deleted.get())
                    throw software.amazon.awssdk.services.lambdacore.model.ResourceNotFoundException
                        .builder()
                        .build();
                  yield GetNetworkConnectorResponse.builder().state("ACTIVE").build();
                }
                case "deleteNetworkConnector" -> {
                  deleted.set(true);
                  yield DeleteNetworkConnectorResponse.builder().build();
                }
                default -> throw new AssertionError(m.getName());
              };
            });
    var vm =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> {
              calls.add(m.getName());
              return switch (m.getName()) {
                case "listMicrovmImagesPaginator" ->
                    new ListMicrovmImagesIterable(
                        (LambdaMicrovmsClient) p, ListMicrovmImagesRequest.builder().build());
                case "listMicrovmImages" -> ListMicrovmImagesResponse.builder().build();
                case "listManagedMicrovmImagesPaginator" ->
                    new ListManagedMicrovmImagesIterable(
                        (LambdaMicrovmsClient) p,
                        ListManagedMicrovmImagesRequest.builder().build());
                case "listManagedMicrovmImages" ->
                    ListManagedMicrovmImagesResponse.builder()
                        .items(
                            ManagedMicrovmImageSummary.builder()
                                .imageArn("old-base")
                                .createdAt(Instant.EPOCH)
                                .build(),
                            ManagedMicrovmImageSummary.builder()
                                .imageArn("latest-base")
                                .createdAt(Instant.now())
                                .build())
                        .build();
                case "createMicrovmImage" -> {
                  imageRequest.set((CreateMicrovmImageRequest) a[0]);
                  yield CreateMicrovmImageResponse.builder()
                      .imageArn("image")
                      .imageVersion("1")
                      .build();
                }
                case "getMicrovmImage" ->
                    GetMicrovmImageResponse.builder()
                        .imageArn("image")
                        .latestActiveImageVersion("1")
                        .build();
                case "deleteMicrovmImage" -> DeleteMicrovmImageResponse.builder().build();
                default -> throw new AssertionError(m.getName());
              };
            });
    var infra = new Infrastructure(iam, s3, vm, core, ec2, dir.resolve("state.json"));
    var spec =
        new Infrastructure.Spec(
            "us-east-1",
            null,
            Networking.operatorRole("build-role"),
            List.of(),
            "bucket",
            false,
            dir,
            CreateMicrovmImageRequest.builder().name("image").build(),
            null,
            null);
    var network =
        new Infrastructure.NetworkSpec("network", "vpc", List.of("subnet-123"), null, null);
    var outputs = infra.up(spec, network, false, Duration.ofSeconds(10));
    assertEquals("sg-test", outputs.get("security_group_id"));
    assertEquals(2, operatorAttempts.get());
    assertEquals("latest-base", imageRequest.get().baseImageArn());
    assertEquals("ENABLED", imageRequest.get().hooks().microvmImageHooks().readyAsString());
    assertEquals(10, imageRequest.get().hooks().microvmHooks().suspendTimeoutInSeconds());
    infra.destroy(true);
    assertTrue(calls.indexOf("deleteNetworkConnector") < calls.indexOf("deleteSecurityGroup"));
    assertTrue(calls.indexOf("deleteSecurityGroup") < calls.indexOf("deleteRole"));
    assertFalse(Files.exists(dir.resolve("state.json")));
  }
}
