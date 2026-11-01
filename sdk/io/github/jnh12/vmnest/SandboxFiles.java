package io.github.jnh12.vmnest;

import com.fasterxml.jackson.databind.*;
import java.io.IOException;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;

/**
 * Guest filesystem helpers. Arguments are passed separately, never interpolated into shell code.
 */
public final class SandboxFiles {
  private final AgentClient agent;
  private final String workdir;
  private final List<Path> roots;
  private final Duration timeout;

  public SandboxFiles(
      AgentClient agent, String workdir, List<Path> allowedHostRoots, Duration timeout)
      throws IOException {
    if (!workdir.startsWith("/"))
      throw new IllegalArgumentException("Absolute guest workdir required");
    this.agent = agent;
    this.workdir = workdir;
    this.timeout = timeout;
    var resolved = new ArrayList<Path>();
    for (var root : allowedHostRoots) resolved.add(root.toRealPath());
    roots = List.copyOf(resolved);
  }

  private String absolute(String p) {
    String full = p.startsWith("/") ? p : workdir + "/" + p;
    var parts = new ArrayDeque<String>();
    for (String part : full.split("/")) {
      if (part.isEmpty() || part.equals(".")) continue;
      if (part.equals("..")) {
        if (!parts.isEmpty()) parts.removeLast();
      } else parts.addLast(part);
    }
    return "/" + String.join("/", parts);
  }

  private JsonNode python(String script, String... args) throws IOException, InterruptedException {
    var argv = new ArrayList<String>(List.of("-c", script));
    argv.addAll(Arrays.asList(args));
    var result = agent.exec("python3", argv, null, null, timeout);
    if (!result.success())
      throw new IOException("Guest filesystem command failed with exit " + result.exitCode());
    return new ObjectMapper().readTree(result.stdoutText());
  }

  public byte[] read(String path) throws IOException, InterruptedException {
    return agent.readFile(absolute(path));
  }

  public void write(String path, byte[] bytes) throws IOException, InterruptedException {
    agent.writeFile(absolute(path), bytes);
  }

  public JsonNode list(String path) throws IOException, InterruptedException {
    return python(
        "import os,json,sys\n"
            + "p=sys.argv[1]\n"
            + "r=[]\n"
            + "for n in sorted(os.listdir(p)):\n"
            + " f=os.path.join(p,n);s=os.lstat(f);r.append(dict(name=n,is_dir=os.path.isdir(f),is_symlink=os.path.islink(f),size=s.st_size,mode=oct(s.st_mode),mtime=int(s.st_mtime)))\n"
            + "print(json.dumps(r))",
        absolute(path));
  }

  public JsonNode stat(String path) throws IOException, InterruptedException {
    return python(
        "import"
            + " os,json,sys;p=sys.argv[1];s=os.lstat(p);print(json.dumps(dict(path=p,size=s.st_size,mode=oct(s.st_mode),is_dir=os.path.isdir(p),is_file=os.path.isfile(p),is_symlink=os.path.islink(p),mtime=int(s.st_mtime),uid=s.st_uid,gid=s.st_gid)))",
        absolute(path));
  }

  public boolean exists(String path) throws IOException, InterruptedException {
    return python(
            "import os,json,sys;print(json.dumps(os.path.exists(sys.argv[1])))", absolute(path))
        .asBoolean();
  }

  public void mkdir(String path) throws IOException, InterruptedException {
    python("import os,sys;os.makedirs(sys.argv[1],exist_ok=True);print('null')", absolute(path));
  }

  public void remove(String path, boolean recursive) throws IOException, InterruptedException {
    python(
        "import os,sys,shutil\n"
            + "p=sys.argv[1]\n"
            + "if os.path.isdir(p) and not os.path.islink(p):\n"
            + " if sys.argv[2]=='true': shutil.rmtree(p)\n"
            + " else: os.rmdir(p)\n"
            + "elif os.path.lexists(p): os.unlink(p)\n"
            + "print('null')",
        absolute(path),
        Boolean.toString(recursive));
  }

  public void copy(String src, String dst) throws IOException, InterruptedException {
    python(
        "import os,sys,shutil\n"
            + "s,d=sys.argv[1:]\n"
            + "if os.path.isdir(s): shutil.copytree(s,d,dirs_exist_ok=True)\n"
            + "else: shutil.copy2(s,d)\n"
            + "print('null')",
        absolute(src),
        absolute(dst));
  }

  public void rename(String src, String dst) throws IOException, InterruptedException {
    python(
        "import sys,shutil;shutil.move(sys.argv[1],sys.argv[2]);print('null')",
        absolute(src),
        absolute(dst));
  }

  private Path allowed(Path path) throws IOException {
    Path absolute = path.toAbsolutePath().normalize(), existing = absolute;
    while (!Files.exists(existing, LinkOption.NOFOLLOW_LINKS)) {
      existing = existing.getParent();
      if (existing == null) throw new IOException("No existing parent");
    }
    Path resolved = existing.toRealPath().resolve(existing.relativize(absolute)).normalize();
    if (roots.stream().noneMatch(resolved::startsWith)) throw new IOException("Host path denied");
    return resolved;
  }

  public void copyFromHost(Path src, String dst) throws IOException, InterruptedException {
    write(dst, Files.readAllBytes(allowed(src)));
  }

  public void copyToHost(String src, Path dst) throws IOException, InterruptedException {
    Path target = allowed(dst);
    byte[] bytes = read(src);
    Files.createDirectories(target.getParent());
    // Recheck after directory creation to catch pre-existing symlinks. Caller must prevent
    // concurrent host path replacement.
    target = allowed(target);
    Files.write(target, bytes);
  }
}
