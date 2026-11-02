package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.util.*;
import software.amazon.awssdk.services.ec2.Ec2Client;
import software.amazon.awssdk.services.ec2.model.*;
import software.amazon.awssdk.services.iam.IamClient;
import software.amazon.awssdk.services.iam.model.CreateRoleRequest;
import software.amazon.awssdk.services.lambdacore.model.CreateNetworkConnectorRequest;

/** Native helpers for optional egress provisioning. Returned resources remain caller-owned. */
public final class Networking {
  private Networking() {}

  public static String defaultVpc(Ec2Client ec2) {
    var vpcs = ec2.describeVpcs(r -> r.filters(f -> f.name("isDefault").values("true"))).vpcs();
    if (vpcs.isEmpty()) throw new IllegalStateException("No default VPC; supply vpcId");
    return vpcs.getFirst().vpcId();
  }

  public static List<String> subnets(Ec2Client ec2, String vpc, List<String> idsOrNames) {
    var out = new ArrayList<String>();
    if (idsOrNames.isEmpty()) {
      for (var page :
          ec2.describeSubnetsPaginator(r -> r.filters(f -> f.name("vpc-id").values(vpc))))
        for (var subnet : page.subnets()) out.add(subnet.subnetId());
    } else
      for (String value : idsOrNames) {
        var filters = new ArrayList<Filter>();
        filters.add(Filter.builder().name("vpc-id").values(vpc).build());
        filters.add(
            Filter.builder()
                .name(value.startsWith("subnet-") ? "subnet-id" : "tag:Name")
                .values(value)
                .build());
        var found = ec2.describeSubnets(r -> r.filters(filters)).subnets();
        if (found.isEmpty())
          throw new IllegalArgumentException("Subnet not found in requested VPC: " + value);
        for (var subnet : found) out.add(subnet.subnetId());
      }
    return List.copyOf(out);
  }

  public static String createSecurityGroup(Ec2Client ec2, String name, String vpc) {
    return ec2.createSecurityGroup(
            r -> r.groupName(name).vpcId(vpc).description("VMNest egress only"))
        .groupId();
  }

  public static void deleteSecurityGroup(Ec2Client ec2, String id, boolean confirm) {
    if (!confirm) throw new IllegalArgumentException("confirm=true required");
    try {
      ec2.deleteSecurityGroup(r -> r.groupId(id));
    } catch (Ec2Exception e) {
      String code = e.awsErrorDetails() == null ? "" : e.awsErrorDetails().errorCode();
      if (!Set.of("InvalidGroup.NotFound", "InvalidGroupId.NotFound").contains(code)) throw e;
    }
  }

  public static CreateRoleRequest operatorRole(String name) throws IOException {
    String trust =
        new ObjectMapper()
            .writeValueAsString(
                Map.of(
                    "Version",
                    "2012-10-17",
                    "Statement",
                    List.of(
                        Map.of(
                            "Effect",
                            "Allow",
                            "Principal",
                            Map.of("Service", "lambda.amazonaws.com"),
                            "Action",
                            "sts:AssumeRole"))));
    return CreateRoleRequest.builder().roleName(name).assumeRolePolicyDocument(trust).build();
  }

  public static void grantOperatorPermissions(IamClient iam, String name) throws IOException {
    String policy =
        new ObjectMapper()
            .writeValueAsString(
                Map.of(
                    "Version",
                    "2012-10-17",
                    "Statement",
                    List.of(
                        Map.of(
                            "Effect",
                            "Allow",
                            "Action",
                            List.of(
                                "ec2:CreateNetworkInterface",
                                "ec2:CreateTags",
                                "ec2:DescribeNetworkInterfaces",
                                "ec2:DeleteNetworkInterface"),
                            "Resource",
                            "*"))));
    iam.putRolePolicy(r -> r.roleName(name).policyName("vmnest-network").policyDocument(policy));
  }

  public static void deleteOperatorRole(IamClient iam, String name, boolean confirm) {
    if (!confirm) throw new IllegalArgumentException("confirm=true required");
    deleteRole(iam, name);
  }

  public static void deleteRole(IamClient iam, String name) {
    try {
      for (var page : iam.listRolePoliciesPaginator(r -> r.roleName(name)))
        for (String policy : page.policyNames())
          try {
            iam.deleteRolePolicy(r -> r.roleName(name).policyName(policy));
          } catch (software.amazon.awssdk.services.iam.model.NoSuchEntityException ignored) {
          }
      for (var page : iam.listAttachedRolePoliciesPaginator(r -> r.roleName(name)))
        for (var policy : page.attachedPolicies())
          try {
            iam.detachRolePolicy(r -> r.roleName(name).policyArn(policy.policyArn()));
          } catch (software.amazon.awssdk.services.iam.model.NoSuchEntityException ignored) {
          }
      iam.deleteRole(r -> r.roleName(name));
    } catch (software.amazon.awssdk.services.iam.model.NoSuchEntityException ignored) {
    }
  }

  public static software.amazon.awssdk.services.lambdacore.model.CreateNetworkConnectorResponse
      createConnector(
          software.amazon.awssdk.services.lambdacore.LambdaCoreClient core,
          CreateNetworkConnectorRequest request)
          throws InterruptedException {
    return Retry.call(
        () -> core.createNetworkConnector(request),
        e -> {
          String message = Objects.toString(e.getMessage(), "").toLowerCase(Locale.ROOT);
          return e instanceof software.amazon.awssdk.services.lambdacore.model.LambdaCoreException
              && message.contains("assume")
              && message.contains("role");
        },
        java.time.Duration.ofSeconds(120));
  }

  public static CreateNetworkConnectorRequest connector(
      String name, List<String> subnets, List<String> groups, String operatorRole) {
    return CreateNetworkConnectorRequest.builder()
        .name(name)
        .operatorRole(operatorRole)
        .configuration(
            c ->
                c.vpcEgressConfiguration(
                    v ->
                        v.subnetIds(subnets)
                            .securityGroupIds(groups)
                            .networkProtocol("IPv4")
                            .associatedComputeResourceTypesWithStrings("MicroVm")))
        .build();
  }
}
