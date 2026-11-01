import static org.junit.jupiter.api.Assertions.*;

import io.github.jnh12.vmnest.*;
import java.net.*;
import java.net.http.*;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import software.amazon.awssdk.services.lambdamicrovms.*;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

@EnabledIfEnvironmentVariable(named = "VMNEST_JAVA_LIVE", matches = "1")
class LiveTest {
  @Test
  void fullLifecycle() throws Exception {
    try (var aws =
        LambdaMicrovmsClient.builder()
            .region(
                software.amazon.awssdk.regions.Region.of(
                    System.getenv().getOrDefault("AWS_REGION", "us-east-1")))
            .overrideConfiguration(c -> c.apiCallTimeout(Duration.ofSeconds(60)))
            .build()) {
      String id = null;
      Path recovery = Path.of("build/live-vm-id.txt");
      Files.createDirectories(recovery.getParent());
      try {
        var launch =
            aws.runMicrovm(
                r ->
                    r.imageIdentifier(System.getenv("AGENT_SANDBOX_IMAGE_ARN"))
                        .executionRoleArn(System.getenv("AGENT_SANDBOX_EXECUTION_ROLE_ARN"))
                        .clientToken(UUID.randomUUID().toString())
                        .maximumDurationInSeconds(600)
                        .idlePolicy(
                            p ->
                                p.maxIdleDurationSeconds(300)
                                    .suspendedDurationSeconds(300)
                                    .autoResumeEnabled(false)));
        id = launch.microvmId();
        Files.writeString(recovery, id);
        System.out.println("Launched disposable test VM; recovery identifier recorded locally");
        var control = new ControlPlane(aws);
        control.waitUntilRunning(id, Duration.ofMinutes(2), Duration.ofSeconds(1));
        var sandbox = Sandbox.attach("java-live", id, control, 8080, Duration.ofSeconds(30));
        assertTrue(sandbox.agent().health());
        var result =
            sandbox.exec(
                "python3",
                List.of("-c", "import os;print(os.environ['VMNEST_TEST'])"),
                "/tmp",
                Map.of("VMNEST_TEST", "42"),
                Duration.ofSeconds(10));
        assertEquals("42", result.stdoutText().trim());
        assertEquals(7, sandbox.shell("exit 7", null, null, Duration.ofSeconds(10)).exitCode());
        Path host = Files.createTempDirectory("vmnest-live-");
        String guest = "/tmp/vmnest-java-" + UUID.randomUUID();
        try {
          var files =
              new SandboxFiles(sandbox.agent(), guest, List.of(host), Duration.ofSeconds(10));
          files.mkdir(".");
          files.write("binary", new byte[] {0, -1, 3});
          assertArrayEquals(new byte[] {0, -1, 3}, files.read("binary"));
          assertTrue(files.exists("binary"));
          assertEquals(3, files.stat("binary").path("size").asInt());
          assertEquals(1, files.list(".").size());
          files.copy("binary", "copy");
          files.rename("copy", "renamed");
          files.remove("renamed", false);
          assertFalse(files.exists("renamed"));
          Files.write(host.resolve("upload"), new byte[] {4, 5});
          files.copyFromHost(host.resolve("upload"), "upload");
          files.copyToHost("upload", host.resolve("download"));
          assertArrayEquals(new byte[] {4, 5}, Files.readAllBytes(host.resolve("download")));
          assertThrows(
              java.io.IOException.class,
              () -> files.copyToHost("upload", host.resolve("../denied")));
          var start =
              sandbox.exec(
                  "python3",
                  List.of(
                      "-c",
                      "import"
                          + " subprocess;subprocess.Popen(['python3','-m','http.server','8765','--bind','0.0.0.0','--directory',"
                          + "'"
                          + guest
                          + "'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)"),
                  null,
                  null,
                  Duration.ofSeconds(10));
          assertTrue(start.success());
          try (var forward = sandbox.forward(8765, 0)) {
            var http = HttpClient.newHttpClient();
            HttpResponse<byte[]> response = null;
            for (int attempt = 0; attempt < 10; attempt++) {
              response =
                  http.send(
                      HttpRequest.newBuilder(
                              URI.create("http://localhost:" + forward.localPort() + "/binary"))
                          .build(),
                      HttpResponse.BodyHandlers.ofByteArray());
              if (response.statusCode() == 200) break;
              Thread.sleep(500);
            }
            assertEquals(200, response.statusCode());
            assertArrayEquals(new byte[] {0, -1, 3}, response.body());
          }
          sandbox.suspend();
          assertEquals(
              "suspended",
              sandbox.waitFor("suspended", Duration.ofSeconds(60), Duration.ofSeconds(1)));
          sandbox.resume();
          assertTrue(sandbox.agent().health());
          assertArrayEquals(new byte[] {0, -1, 3}, sandbox.readFile(guest + "/binary"));
          new SandboxFiles(sandbox.agent(), guest, List.of(host), Duration.ofSeconds(10))
              .remove(".", true);
          try (var metrics =
                  software.amazon.awssdk.services.cloudwatch.CloudWatchClient.builder()
                      .region(software.amazon.awssdk.regions.Region.US_EAST_1)
                      .build();
              var logs =
                  software.amazon.awssdk.services.cloudwatchlogs.CloudWatchLogsClient.builder()
                      .region(software.amazon.awssdk.regions.Region.US_EAST_1)
                      .build()) {
            var observation = new Observability(metrics, logs);
            assertEquals(2, observation.metrics(id, Duration.ofMinutes(5)).size());
            String group = System.getenv("VMNEST_JAVA_LOG_GROUP");
            if (group != null)
              observation.readLogs(group, 10, java.time.Instant.now().minusSeconds(300), null);
          }
        } finally {
          try (var entries = Files.list(host)) {
            for (var entry : entries.toList()) Files.deleteIfExists(entry);
          }
          Files.deleteIfExists(host);
        }
      } finally {
        if (id != null) {
          String cleanup = id;
          aws.terminateMicrovm(r -> r.microvmIdentifier(cleanup));
          boolean done = false;
          for (int attempt = 0; attempt < 60; attempt++) {
            try {
              if ("TERMINATED"
                  .equals(aws.getMicrovm(r -> r.microvmIdentifier(cleanup)).stateAsString())) {
                done = true;
                break;
              }
            } catch (ResourceNotFoundException e) {
              done = true;
              break;
            }
            Thread.sleep(1000);
          }
          assertTrue(
              done, "VM termination did not complete; use build/live-vm-id.txt to reconcile");
          Files.deleteIfExists(recovery);
          System.out.println("Confirmed disposable VM terminated");
        }
      }
    }
  }
}
