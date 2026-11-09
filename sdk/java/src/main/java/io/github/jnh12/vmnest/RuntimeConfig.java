package io.github.jnh12.vmnest;

import java.util.*;
import software.amazon.awssdk.services.lambdamicrovms.model.RunMicrovmRequest;

/** Immutable runtime defaults; no secret credentials are copied into configuration records. */
public record RuntimeConfig(
    String imageArn, String roleArn, String region, String workdir, String egressConnector) {
  public static String installationHelp() {
    return "Java 21+; build/install vmnest-java with ./gradlew publishToMavenLocal; configure AWS"
        + " credentials, image and execution role. No Python host process is required.";
  }

  public static RuntimeConfig fromEnvironment() {
    var env = fromEnvironment(System.getenv());
    try {
      var config = InfraConfig.load(null);
      return fromOutputs(
          new RuntimeConfig(
              env.imageArn(),
              env.roleArn(),
              pick(env.region(), config.region()),
              env.workdir(),
              env.egressConnector()),
          Infrastructure.listStacks(InfraConfig.defaultStatePath())
              .getOrDefault(config.project() + "/" + config.stack(), Map.of()));
    } catch (Exception ignored) {
      return env;
    }
  }

  public static RuntimeConfig fromEnvironment(Map<String, String> env) {
    return new RuntimeConfig(
        env.get("AGENT_SANDBOX_IMAGE_ARN"),
        env.get("AGENT_SANDBOX_EXECUTION_ROLE_ARN"),
        env.getOrDefault(
            "AGENT_SANDBOX_REGION", env.getOrDefault("AWS_REGION", env.get("AWS_DEFAULT_REGION"))),
        env.getOrDefault("AGENT_SANDBOX_WORKDIR", "/work"),
        env.get("AGENT_SANDBOX_EGRESS_CONNECTOR"));
  }

  public static RuntimeConfig fromOutputs(RuntimeConfig env, Map<String, String> outputs) {
    return new RuntimeConfig(
        pick(env.imageArn(), outputs.get("image_arn")),
        pick(env.roleArn(), outputs.get("execution_role_arn")),
        env.region(),
        env.workdir(),
        pick(env.egressConnector(), outputs.get("egress_network_connector_arn")));
  }

  private static String pick(String value, String fallback) {
    return value == null || value.isBlank() ? fallback : value;
  }

  public RunMicrovmRequest request(String clientToken) {
    if (imageArn == null || imageArn.isBlank() || roleArn == null || roleArn.isBlank())
      throw new IllegalStateException("Image and role are required");
    var builder =
        RunMicrovmRequest.builder()
            .imageIdentifier(imageArn)
            .executionRoleArn(roleArn)
            .clientToken(clientToken)
            .idlePolicy(
                i ->
                    i.maxIdleDurationSeconds(900)
                        .suspendedDurationSeconds(300)
                        .autoResumeEnabled(true));
    if (egressConnector != null && !egressConnector.isBlank())
      builder.egressNetworkConnectors(egressConnector);
    return builder.build();
  }

  public record Diagnostics(
      String region,
      boolean awsCredentials,
      boolean lambdaMicrovmsClient,
      String imageArn,
      String roleArn,
      boolean ready,
      String error) {}

  public Diagnostics diagnostics(ControlPlane control) {
    if (control != null && control.hosted != null) {
      try {
        control.hosted.account();
        return new Diagnostics("us-east-1", false, false, "default", null, true, null);
      } catch (RuntimeException e) {
        return new Diagnostics("us-east-1", false, false, "default", null, false, e.getMessage());
      }
    }
    boolean credentials = false;
    String error = null;
    try {
      var configuration = control.aws().serviceClientConfiguration();
      credentials =
          configuration != null
              && configuration.credentialsProvider().resolveIdentity().join() != null;
    } catch (RuntimeException e) {
      error = e.getMessage();
    }
    boolean client = control != null;
    return new Diagnostics(
        region,
        credentials,
        client,
        imageArn,
        roleArn,
        credentials
            && client
            && imageArn != null
            && !imageArn.isBlank()
            && roleArn != null
            && !roleArn.isBlank(),
        error);
  }

  public boolean check(ControlPlane control) {
    if (control.hosted != null) { control.hosted.account(); return true; }
    request("check");
    control.aws().getMicrovmImage(r -> r.imageIdentifier(imageArn));
    return true;
  }
}
