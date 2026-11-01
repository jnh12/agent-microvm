package io.github.jnh12.vmnest;

import java.io.*;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.TimeoutException;
import java.util.zip.*;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.lambdamicrovms.LambdaMicrovmsClient;
import software.amazon.awssdk.services.lambdamicrovms.model.*;
import software.amazon.awssdk.services.s3.S3Client;

public final class Images {
  private final LambdaMicrovmsClient aws;

  public Images(LambdaMicrovmsClient aws) {
    this.aws = aws;
  }

  public static void extractBundledGuest(Path destination) throws IOException {
    Path root = destination.toAbsolutePath().normalize();
    Files.createDirectories(root);
    try (var source = Images.class.getResourceAsStream("/vmnest-guest.zip")) {
      if (source == null) throw new IOException("Bundled guest source missing");
      try (var zip = new ZipInputStream(source)) {
        ZipEntry entry;
        while ((entry = zip.getNextEntry()) != null) {
          Path target = root.resolve(entry.getName()).normalize();
          if (!target.startsWith(root)) throw new IOException("Invalid archive path");
          if (entry.isDirectory()) Files.createDirectories(target);
          else {
            Files.createDirectories(target.getParent());
            Files.copy(zip, target);
          }
        }
      }
    }
  }

  public List<MicrovmImageSummary> list() {
    var out = new ArrayList<MicrovmImageSummary>();
    for (var page : aws.listMicrovmImagesPaginator(ListMicrovmImagesRequest.builder().build()))
      out.addAll(page.items());
    return out;
  }

  public List<ManagedMicrovmImageSummary> managed() {
    var out = new ArrayList<ManagedMicrovmImageSummary>();
    for (var page :
        aws.listManagedMicrovmImagesPaginator(ListManagedMicrovmImagesRequest.builder().build()))
      out.addAll(page.items());
    return out;
  }

  public GetMicrovmImageResponse inspect(String arn) {
    return aws.getMicrovmImage(r -> r.imageIdentifier(arn));
  }

  public void remove(String arn, boolean confirm) {
    if (!confirm) throw new IllegalArgumentException("confirm=true required");
    aws.deleteMicrovmImage(r -> r.imageIdentifier(arn));
  }

  public List<String> prune(boolean confirm) {
    if (!confirm) throw new IllegalArgumentException("confirm=true required");
    var used = new HashSet<String>();
    for (var page : aws.listMicrovmsPaginator(ListMicrovmsRequest.builder().build()))
      for (var vm : page.items())
        if (!"TERMINATED".equals(vm.stateAsString())) {
          if (vm.imageArn() == null)
            throw new IllegalStateException("Cannot safely identify VM image");
          used.add(vm.imageArn());
        }
    var removed = new ArrayList<String>();
    for (var image : list())
      if (!used.contains(image.imageArn())) {
        remove(image.imageArn(), true);
        removed.add(image.imageArn());
      }
    return removed;
  }

  /**
   * ZIP contents are streamed to disk; symlinks are rejected rather than escaping the guest source
   * tree.
   */
  public static void archive(Path source, Path destination) throws IOException {
    Path root = source.toRealPath();
    if (destination.toAbsolutePath().normalize().startsWith(root))
      throw new IllegalArgumentException("Archive must be outside source tree");
    if (!Files.isRegularFile(root.resolve("Dockerfile")))
      throw new IOException("Guest source requires Dockerfile");
    try (var zip = new ZipOutputStream(Files.newOutputStream(destination));
        var paths = Files.walk(root)) {
      for (var path : paths.sorted().toList()) {
        if (Files.isSymbolicLink(path)) throw new IOException("Symlink in image source");
        String name = root.relativize(path).toString().replace('\\', '/');
        if (name.isEmpty()
            || Arrays.stream(name.split("/"))
                .anyMatch(s -> Set.of(".git", ".venv", "__pycache__").contains(s))) continue;
        if (Files.isRegularFile(path)) {
          var entry = new ZipEntry(name);
          entry.setTime(0);
          zip.putNextEntry(entry);
          Files.copy(path, zip);
          zip.closeEntry();
        }
      }
    }
  }

  public CreateMicrovmImageResponse build(
      Path source, S3Client s3, String bucket, String key, CreateMicrovmImageRequest settings)
      throws IOException {
    Path archive = Files.createTempFile("vmnest-image-", ".zip");
    try {
      archive(source, archive);
      s3.putObject(r -> r.bucket(bucket).key(key), RequestBody.fromFile(archive));
      return aws.createMicrovmImage(
          settings.toBuilder().codeArtifact(c -> c.uri("s3://" + bucket + "/" + key)).build());
    } finally {
      Files.deleteIfExists(archive);
    }
  }

  public GetMicrovmImageResponse waitActive(String arn, Duration timeout)
      throws InterruptedException, TimeoutException {
    return waitActive(arn, null, timeout);
  }

  public GetMicrovmImageResponse waitActive(String arn, String expectedVersion, Duration timeout)
      throws InterruptedException, TimeoutException {
    if (timeout.isNegative() || timeout.isZero())
      throw new IllegalArgumentException("Positive timeout required");
    long start = System.nanoTime();
    while (System.nanoTime() - start < timeout.toNanos()) {
      var image = inspect(arn);
      if (image.latestActiveImageVersion() != null
          && (expectedVersion == null || expectedVersion.equals(image.latestActiveImageVersion())))
        return image;
      if (image.latestFailedImageVersion() != null
          && (expectedVersion == null || expectedVersion.equals(image.latestFailedImageVersion())))
        throw new IllegalStateException("Image build failed");
      Thread.sleep(1000);
    }
    throw new TimeoutException("Image did not become active");
  }
}
