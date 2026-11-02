import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.*;
import io.github.jnh12.vmnest.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

class LanguageContractTest {
  @TempDir Path temporary;

  @Test
  void everyPublicPythonSdkOperationHasJavaMapping() throws Exception {
    var fixture =
        new ObjectMapper().readTree(getClass().getResourceAsStream("/python-contract.json"));
    var classes =
        Map.of(
            "Sandbox",
            Sandbox.class,
            "AgentClient",
            AgentClient.class,
            "ControlPlane",
            ControlPlane.class);
    for (var entry : classes.entrySet())
      for (var operation : fixture.path("public_api").path(entry.getKey())) {
        String name = operation.asText();
        String[] words = name.split("_");
        var method = new StringBuilder(words[0]);
        for (int i = 1; i < words.length; i++)
          method.append(Character.toUpperCase(words[i].charAt(0))).append(words[i].substring(1));
        String target = method.toString();
        Class<?> type = entry.getValue();
        if (name.equals("aclose")) target = "close";
        if (type == ControlPlane.class
            && !Set.of("run_microvm", "wait_until_running").contains(name)) {
          type = LambdaMicrovmsClient.class;
          if (name.equals("create_auth_token")) target = "createMicrovmAuthToken";
        }
        String expected = target;
        assertTrue(
            Arrays.stream(type.getMethods()).anyMatch(m -> m.getName().equals(expected)),
            entry.getKey() + "." + name);
      }
  }

  @Test
  void pythonDefaultsAndStateRoundTrip() throws Exception {
    var json = new ObjectMapper();
    var fixture = json.readTree(getClass().getResourceAsStream("/python-contract.json"));
    fixture
        .path("states")
        .fields()
        .forEachRemaining(
            e -> assertEquals(e.getValue().asText(), Sandbox.normalizedState(e.getKey())));
    var expected = fixture.path("config");
    var config = new InfraConfig(json.createObjectNode(), temporary);
    assertEquals(expected.path("project").asText(), config.project());
    assertEquals(expected.path("stack").asText(), config.stack());
    assertEquals(expected.path("region").asText(), config.region());
    var spec = config.spec(Map.of());
    assertEquals(expected.at("/image/name").asText(), spec.image().name());
    assertEquals(expected.at("/role/name").asText(), spec.newRole().roleName());
    var idle =
        new RuntimeConfig("image", "role", null, "/work", null).request("token").idlePolicy();
    assertEquals(fixture.at("/idle/maxIdleDurationSeconds").asInt(), idle.maxIdleDurationSeconds());
    assertEquals(
        fixture.at("/idle/suspendedDurationSeconds").asInt(), idle.suspendedDurationSeconds());
    assertEquals(fixture.at("/idle/autoResumeEnabled").asBoolean(), idle.autoResumeEnabled());
    var hooks = Images.defaultHooks();
    assertEquals(fixture.at("/hooks/port").asInt(), hooks.port());
    for (var field : hooks.microvmHooks().sdkFields()) {
      String name = field.memberName();
      name = Character.toLowerCase(name.charAt(0)) + name.substring(1);
      assertEquals(
          fixture.at("/hooks/microvmHooks/" + name).asText(),
          String.valueOf(field.getValueOrDefault(hooks.microvmHooks())));
    }
    for (var field : hooks.microvmImageHooks().sdkFields()) {
      String name = field.memberName();
      name = Character.toLowerCase(name.charAt(0)) + name.substring(1);
      assertEquals(
          fixture.at("/hooks/microvmImageHooks/" + name).asText(),
          String.valueOf(field.getValueOrDefault(hooks.microvmImageHooks())));
    }
    Path output = Path.of("build/python-contract");
    Files.createDirectories(output);
    Path records = output.resolve("state.json");
    json.writeValue(records.toFile(), fixture.path("registry"));
    var aws =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) ->
                GetMicrovmResponse.builder()
                    .microvmId("vm-java")
                    .endpoint("example.com")
                    .imageArn("image-java")
                    .state("RUNNING")
                    .build());
    try (var registry = new SandboxRegistry(records, new ControlPlane(aws))) {
      assertEquals("vm-python", registry.list().getFirst().microvmId());
      registry.attach("java", "vm-java", 8080);
    }
    // A fake client has no region configuration; fill the public Python record field for the round
    // trip.
    var recordsJson = json.readTree(records.toFile());
    ((com.fasterxml.jackson.databind.node.ObjectNode) recordsJson.at("/sandboxes/java"))
        .put("region", "us-east-1");
    json.writeValue(records.toFile(), recordsJson);
    var infraFile = output.resolve("infra.json");
    Files.writeString(
        infraFile,
        "{\"stacks\":{\"contract/dev\":{\"resources\":{\"image\":{\"id\":\"image-java\",\"kind\":\"microvm-image\",\"managed\":false}},\"outputs\":{\"image_arn\":\"image-java\"}}}}");
    var imageClient =
        ExtendedTest.fake(
            LambdaMicrovmsClient.class,
            (p, m, a) -> GetMicrovmImageResponse.builder().latestActiveImageVersion("2").build());
    var contractConfig = new InfraConfig(json.readTree("{\"project\":\"contract\"}"), temporary);
    Infrastructure.forConfig(contractConfig, null, null, imageClient, null, null, infraFile)
        .refresh();
  }
}
