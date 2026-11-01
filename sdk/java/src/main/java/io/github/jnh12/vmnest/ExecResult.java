package io.github.jnh12.vmnest;

import java.nio.charset.StandardCharsets;

public record ExecResult(int exitCode, byte[] stdout, byte[] stderr) {
  public ExecResult {
    stdout = stdout.clone();
    stderr = stderr.clone();
  }

  public byte[] stdout() {
    return stdout.clone();
  }

  public byte[] stderr() {
    return stderr.clone();
  }

  public String stdoutText() {
    return new String(stdout, StandardCharsets.UTF_8);
  }

  public String stderrText() {
    return new String(stderr, StandardCharsets.UTF_8);
  }

  public boolean success() {
    return exitCode == 0;
  }
}
