import io.github.jnh12.vmnest.SandboxFiles;
import io.github.jnh12.vmnest.HostedClient;

class FilesystemConsumer {
    String account(HostedClient cloud) {
        return cloud.account().path("id").asText();
    }
    long size(SandboxFiles files) throws Exception {
        return files.stat("example").path("size").asLong();
    }
    int count(SandboxFiles files) throws Exception {
        return files.list(".").size();
    }
}
