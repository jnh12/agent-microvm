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
    return fromEnvironment(System.getenv());
  }

  public static RuntimeConfig fromEnvironment(Map<String, String> env) {
    return new RuntimeConfig(
        env.get("AGENT_SANDBOX_IMAGE_ARN"),
        env.get("AGENT_SANDBOX_EXECUTION_ROLE_ARN"),
        env.getOrDefault("AGENT_SANDBOX_REGION", env.get("AWS_REGION")),
        env.getOrDefault("AGENT_SANDBOX_WORKDIR", "/work"),
        env.get("AGENT_SANDBOX_EGRESS_CONNECTOR"));
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

  public boolean check(ControlPlane control) {
    request("check");
    control.aws().getMicrovmImage(r -> r.imageIdentifier(imageArn));
    return true;
  }
}
