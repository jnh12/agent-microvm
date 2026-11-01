package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import software.amazon.awssdk.services.iam.IamClient;
import software.amazon.awssdk.services.iam.model.*;
import software.amazon.awssdk.services.lambdacore.LambdaCoreClient;
import software.amazon.awssdk.services.lambdacore.model.CreateNetworkConnectorRequest;
import software.amazon.awssdk.services.lambdamicrovms.LambdaMicrovmsClient;
import software.amazon.awssdk.services.lambdamicrovms.model.*;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;

/**
 * Native provisioning with typed AWS configuration and a per-stack ownership journal. Existing
 * resources are reused explicitly, never silently adopted for destruction. Configure AWS client
 * regions consistently. One writer per stack journal.
 */
public final class Infrastructure {
  public record Spec(
      String region,
      String existingRoleArn,
      CreateRoleRequest newRole,
      List<String> extraPolicyArns,
      String bucket,
      boolean reuseBucket,
      Path guestSource,
      CreateMicrovmImageRequest image,
      String existingConnectorArn,
      CreateNetworkConnectorRequest newConnector) {
    public Spec {
      Objects.requireNonNull(region);
      Objects.requireNonNull(bucket);
      Objects.requireNonNull(guestSource);
      Objects.requireNonNull(image);
      extraPolicyArns = List.copyOf(extraPolicyArns);
      if ((existingRoleArn == null) == (newRole == null))
        throw new IllegalArgumentException("Specify exactly one reused or new role");
      if (existingConnectorArn != null && newConnector != null)
        throw new IllegalArgumentException("Specify reused or new connector");
    }
  }

  public record State(
      Map<String, String> outputs, Set<String> managed, List<String> attachedPolicies) {
    public State {
      outputs = new LinkedHashMap<>(outputs);
      managed = new HashSet<>(managed);
      attachedPolicies = new ArrayList<>(attachedPolicies);
    }
  }

  private final IamClient iam;
  private final S3Client s3;
  private final LambdaMicrovmsClient vm;
  private final LambdaCoreClient core;
  private final Path journal;
  private String sharedStack;
  private software.amazon.awssdk.services.ec2.Ec2Client ec2;
  private final ObjectMapper json = new ObjectMapper();

  public Infrastructure(
      IamClient iam, S3Client s3, LambdaMicrovmsClient vm, LambdaCoreClient core, Path journal) {
    this.iam = iam;
    this.s3 = s3;
    this.vm = vm;
    this.core = core;
    this.journal = journal.toAbsolutePath();
  }

  public record NetworkSpec(
      String name,
      String vpcId,
      List<String> subnetIds,
      String securityGroupId,
      String operatorRoleArn) {
    public NetworkSpec {
      subnetIds = List.copyOf(subnetIds);
    }
  }

  public Infrastructure(
      IamClient iam,
      S3Client s3,
      LambdaMicrovmsClient vm,
      LambdaCoreClient core,
      software.amazon.awssdk.services.ec2.Ec2Client ec2,
      Path journal) {
    this(iam, s3, vm, core, journal);
    this.ec2 = ec2;
  }

  public static Infrastructure forConfig(
      InfraConfig config,
      IamClient iam,
      S3Client s3,
      LambdaMicrovmsClient vm,
      LambdaCoreClient core,
      software.amazon.awssdk.services.ec2.Ec2Client ec2,
      Path stateFile) {
    var infrastructure =
        new Infrastructure(
            iam, s3, vm, core, ec2, stateFile == null ? InfraConfig.defaultStatePath() : stateFile);
    infrastructure.sharedStack = config.project() + "/" + config.stack();
    return infrastructure;
  }

  public Map<String, String> up(InfraConfig config, boolean rebuild, Duration timeout)
      throws Exception {
    return up(config.spec(outputs()), config.network(), rebuild, timeout);
  }

  public static Map<String, Map<String, String>> listStacks(Path stateFile) throws IOException {
    return InfrastructureState.list(stateFile == null ? InfraConfig.defaultStatePath() : stateFile);
  }

  public Map<String, String> plan(InfraConfig config) throws IOException {
    var result = new LinkedHashMap<>(plan(config.spec(outputs())));
    var network = config.network();
    if (network != null) {
      result.put("connector", "create");
      result.put(
          "security_group",
          network.securityGroupId() == null || network.securityGroupId().isBlank()
              ? "create"
              : "reuse");
      result.put(
          "network_operator_role",
          network.operatorRoleArn() == null || network.operatorRoleArn().isBlank()
              ? "create"
              : "reuse");
    }
    return Map.copyOf(result);
  }

  public Map<String, String> plan(Spec spec) {
    return Map.of(
        "role",
        spec.existingRoleArn() != null ? "reuse" : "create",
        "bucket",
        spec.reuseBucket() ? "reuse" : "create",
        "image",
        "reuse active or build",
        "connector",
        spec.newConnector() != null
            ? "create"
            : spec.existingConnectorArn() != null ? "reuse" : "none");
  }

  private State load() throws IOException {
    if (sharedStack != null) return InfrastructureState.load(journal, sharedStack);
    return Files.exists(journal)
        ? json.readValue(journal.toFile(), State.class)
        : new State(Map.of(), Set.of(), List.of());
  }

  private void save(State state) throws IOException {
    if (sharedStack != null) {
      InfrastructureState.save(journal, sharedStack, state);
      return;
    }
    Files.createDirectories(journal.getParent());
    Path temp = Files.createTempFile(journal.getParent(), "infra-", ".tmp");
    try {
      json.writeValue(temp.toFile(), state);
      try {
        Files.move(
            temp, journal, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
      } catch (AtomicMoveNotSupportedException e) {
        Files.move(temp, journal, StandardCopyOption.REPLACE_EXISTING);
      }
    } finally {
      Files.deleteIfExists(temp);
    }
  }

  public Map<String, String> outputs() throws IOException {
    return Map.copyOf(load().outputs());
  }

  public static Map<String, Map<String, String>> list(Path directory) throws IOException {
    var result = new TreeMap<String, Map<String, String>>();
    if (!Files.isDirectory(directory)) return result;
    try (var files = Files.list(directory)) {
      for (var file : files.filter(p -> p.toString().endsWith(".json")).toList()) {
        var state = new ObjectMapper().readValue(file.toFile(), State.class);
        result.put(file.getFileName().toString(), Map.copyOf(state.outputs()));
      }
    }
    return result;
  }

  public Map<String, String> refresh() throws IOException {
    var state = load();
    String image = state.outputs().get("image_arn");
    if (image != null) {
      try {
        var current = vm.getMicrovmImage(r -> r.imageIdentifier(image));
        if (current.latestActiveImageVersion() != null)
          state.outputs().put("image_version", current.latestActiveImageVersion());
      } catch (ResourceNotFoundException e) {
        state.outputs().remove("image_arn");
        state.outputs().remove("image_version");
        state.managed().remove("image");
      }
    }
    save(state);
    return Map.copyOf(state.outputs());
  }

  public Map<String, String> up(Spec spec, boolean rebuild, Duration imageTimeout)
      throws Exception {
    return up(spec, null, rebuild, imageTimeout);
  }

  public Map<String, String> up(
      Spec spec, NetworkSpec network, boolean rebuild, Duration imageTimeout) throws Exception {
    var state = load();
    var out = state.outputs();
    if (out.containsKey("build_bucket") && !out.get("build_bucket").equals(spec.bucket()))
      throw new IllegalArgumentException("Use a separate journal for another bucket");
    if (!out.containsKey("build_bucket") && !spec.reuseBucket()) {
      // us-east-1 CreateBucket can succeed for an existing owned bucket. Never adopt it.
      boolean absent = false;
      try {
        s3.headBucket(r -> r.bucket(spec.bucket()));
      } catch (S3Exception e) {
        if (e.statusCode() != 404) throw e;
        absent = true;
      }
      if (!absent)
        throw new IllegalArgumentException("Bucket already exists; specify reuseBucket=true");
    }
    String role = out.get("execution_role_arn");
    if (role == null) {
      if (spec.existingRoleArn() != null) role = spec.existingRoleArn();
      else {
        var created = iam.createRole(spec.newRole());
        role = created.role().arn();
        state.managed().add("role");
        out.put("role_name", created.role().roleName());
      }
      out.put("execution_role_arn", role);
      save(state);
    }
    if (!out.containsKey("build_bucket")) {
      if (spec.reuseBucket()) {
        s3.headBucket(r -> r.bucket(spec.bucket()));
      } else {
        var request = CreateBucketRequest.builder().bucket(spec.bucket());
        if (!spec.region().equals("us-east-1"))
          request.createBucketConfiguration(c -> c.locationConstraint(spec.region()));
        s3.createBucket(request.build());
        state.managed().add("bucket");
      }
      out.put("build_bucket", spec.bucket());
      save(state);
    }
    if (state.managed().contains("bucket")) {
      s3.putPublicAccessBlock(
          r ->
              r.bucket(spec.bucket())
                  .publicAccessBlockConfiguration(
                      b ->
                          b.blockPublicAcls(true)
                              .ignorePublicAcls(true)
                              .blockPublicPolicy(true)
                              .restrictPublicBuckets(true)));
      s3.putBucketVersioning(
          r ->
              r.bucket(spec.bucket())
                  .versioningConfiguration(v -> v.status(BucketVersioningStatus.ENABLED)));
      s3.putBucketEncryption(
          r ->
              r.bucket(spec.bucket())
                  .serverSideEncryptionConfiguration(
                      c ->
                          c.rules(
                              rule ->
                                  rule.applyServerSideEncryptionByDefault(
                                      d -> d.sseAlgorithm(ServerSideEncryption.AES256)))));
    }
    if (state.managed().contains("role")) {
      String roleName = out.get("role_name");
      String document =
          json.writeValueAsString(
              Map.of(
                  "Version",
                  "2012-10-17",
                  "Statement",
                  List.of(
                      Map.of(
                          "Effect",
                          "Allow",
                          "Action",
                          List.of("s3:GetObject", "s3:GetObjectVersion"),
                          "Resource",
                          "arn:aws:s3:::" + spec.bucket() + "/*"),
                      Map.of(
                          "Effect",
                          "Allow",
                          "Action",
                          List.of(
                              "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"),
                          "Resource",
                          "arn:aws:logs:*:*:*"))));
      iam.putRolePolicy(
          r -> r.roleName(roleName).policyName("vmnest-build").policyDocument(document));
      for (String policy : spec.extraPolicyArns()) {
        iam.attachRolePolicy(r -> r.roleName(roleName).policyArn(policy));
        if (!state.attachedPolicies().contains(policy)) state.attachedPolicies().add(policy);
        save(state);
      }
    }
    CreateNetworkConnectorRequest connectorRequest = spec.newConnector();
    if (network != null && !out.containsKey("egress_network_connector_arn")) {
      if (ec2 == null)
        throw new IllegalStateException("EC2 client required for network provisioning");
      String vpc =
          network.vpcId() == null || network.vpcId().isBlank()
              ? Networking.defaultVpc(ec2)
              : network.vpcId();
      out.put("vpc_id", vpc);
      String group = out.get("security_group_id");
      if (group == null) {
        group = network.securityGroupId();
        if (group == null || group.isBlank()) {
          group = Networking.createSecurityGroup(ec2, network.name(), vpc);
          state.managed().add("security_group");
        }
        out.put("security_group_id", group);
        save(state);
      }
      String operator = out.get("network_operator_role_arn");
      if (operator == null) {
        operator = network.operatorRoleArn();
        if (operator == null || operator.isBlank()) {
          String name = network.name() + "-operator";
          var created = iam.createRole(Networking.operatorRole(name));
          operator = created.role().arn();
          out.put("network_operator_role_name", name);
          state.managed().add("network_operator_role");
          out.put("network_operator_role_arn", operator);
          save(state);
        } else {
          out.put("network_operator_role_arn", operator);
          save(state);
        }
      }
      if (state.managed().contains("network_operator_role"))
        Networking.grantOperatorPermissions(iam, out.get("network_operator_role_name"));
      List<String> subnets = Networking.subnets(ec2, vpc, network.subnetIds());
      out.put("subnet_ids", json.writeValueAsString(subnets));
      connectorRequest = Networking.connector(network.name(), subnets, List.of(group), operator);
      save(state);
    }
    if (!out.containsKey("egress_network_connector_arn")) {
      if (spec.existingConnectorArn() != null)
        out.put("egress_network_connector_arn", spec.existingConnectorArn());
      else if (connectorRequest != null) {
        out.put(
            "egress_network_connector_arn",
            Networking.createConnector(core, connectorRequest).arn());
        state.managed().add("connector");
      }
      save(state);
    }
    String connector = out.get("egress_network_connector_arn");
    if (connector != null) {
      long start = System.nanoTime();
      while (true) {
        String status = core.getNetworkConnector(r -> r.identifier(connector)).stateAsString();
        if ("ACTIVE".equals(status)) break;
        if ("FAILED".equals(status)) throw new IllegalStateException("Network connector failed");
        if (System.nanoTime() - start > imageTimeout.toNanos())
          throw new java.util.concurrent.TimeoutException("Connector not active");
        Thread.sleep(1000);
      }
    }
    var images = new Images(vm);
    String imageArn = out.get("image_arn");
    if (imageArn == null) {
      for (var image : images.list())
        if (Objects.equals(image.name(), spec.image().name())) {
          imageArn = image.imageArn();
          out.put("image_arn", imageArn);
          break;
        }
      save(state);
    }
    boolean active =
        imageArn != null && images.inspect(imageArn).latestActiveImageVersion() != null;
    if (!active || rebuild) {
      String key = "microvm-images/" + spec.image().name() + ".zip";
      var settings = spec.image().toBuilder().buildRoleArn(role);
      if (connector != null) settings.egressNetworkConnectors(connector);
      var result = images.build(spec.guestSource(), s3, spec.bucket(), key, settings.build());
      if (imageArn == null) state.managed().add("image");
      imageArn = result.imageArn();
      out.put("image_arn", imageArn);
      out.put("guest_key", key);
      out.put("pending_image_version", result.imageVersion());
      save(state);
    }
    out.put(
        "image_version",
        images
            .waitActive(imageArn, out.get("pending_image_version"), imageTimeout)
            .latestActiveImageVersion());
    out.remove("pending_image_version");
    save(state);
    return Map.copyOf(out);
  }

  /** Deletes only resources recorded as managed. Explicit confirmation is required. */
  public void destroy(boolean confirm) throws Exception {
    if (!confirm) throw new IllegalArgumentException("confirm=true required");
    var state = load();
    var out = state.outputs();
    if (state.managed().contains("connector")) {
      String arn = out.get("egress_network_connector_arn");
      try {
        core.deleteNetworkConnector(r -> r.identifier(arn));
      } catch (software.amazon.awssdk.services.lambdacore.model.ResourceNotFoundException ignored) {
      }
      long start = System.nanoTime();
      while (true) {
        try {
          core.getNetworkConnector(r -> r.identifier(arn));
        } catch (software.amazon.awssdk.services.lambdacore.model.ResourceNotFoundException e) {
          break;
        }
        if (System.nanoTime() - start > Duration.ofMinutes(10).toNanos())
          throw new java.util.concurrent.TimeoutException("Connector deletion pending");
        Thread.sleep(1000);
      }
      state.managed().remove("connector");
      out.remove("egress_network_connector_arn");
      save(state);
    }
    if (state.managed().contains("security_group")) {
      if (ec2 == null) throw new IllegalStateException("EC2 client required for cleanup");
      Networking.deleteSecurityGroup(ec2, out.get("security_group_id"), true);
      state.managed().remove("security_group");
      out.remove("security_group_id");
      save(state);
    }
    if (state.managed().contains("network_operator_role")) {
      Networking.deleteOperatorRole(iam, out.get("network_operator_role_name"), true);
      state.managed().remove("network_operator_role");
      out.remove("network_operator_role_name");
      out.remove("network_operator_role_arn");
      save(state);
    }
    if (state.managed().contains("image")) {
      new Images(vm).remove(out.get("image_arn"), true);
      state.managed().remove("image");
      out.remove("image_arn");
      out.remove("image_version");
      save(state);
    }
    if (state.managed().contains("bucket")) {
      String bucket = out.get("build_bucket");
      try {
        for (var page : s3.listObjectVersionsPaginator(r -> r.bucket(bucket))) {
          for (var version : page.versions())
            s3.deleteObject(
                r -> r.bucket(bucket).key(version.key()).versionId(version.versionId()));
          for (var marker : page.deleteMarkers())
            s3.deleteObject(r -> r.bucket(bucket).key(marker.key()).versionId(marker.versionId()));
        }
        s3.deleteBucket(r -> r.bucket(bucket));
      } catch (S3Exception e) {
        if (e.statusCode() != 404) throw e;
      }
      state.managed().remove("bucket");
      out.remove("build_bucket");
      save(state);
    }
    if (state.managed().contains("role")) {
      String name = out.get("role_name");
      try {
        iam.deleteRolePolicy(r -> r.roleName(name).policyName("vmnest-build"));
      } catch (software.amazon.awssdk.services.iam.model.NoSuchEntityException ignored) {
        // Provisioning may have failed before the inline policy was created.
      }
      Networking.deleteRole(iam, name);
      state.managed().remove("role");
      out.remove("role_name");
      out.remove("execution_role_arn");
      save(state);
    }
    if (sharedStack != null) InfrastructureState.clear(journal, sharedStack);
    else Files.deleteIfExists(journal);
  }
}
