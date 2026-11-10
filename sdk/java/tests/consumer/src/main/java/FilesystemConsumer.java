import io.github.jnh12.vmnest.SandboxFiles;

class FilesystemConsumer {
    long size(SandboxFiles files) throws Exception {
        return files.stat("example").path("size").asLong();
    }
    int count(SandboxFiles files) throws Exception {
        return files.list(".").size();
    }
}
