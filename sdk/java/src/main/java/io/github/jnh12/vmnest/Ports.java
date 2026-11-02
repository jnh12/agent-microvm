package io.github.jnh12.vmnest;

/** Port configuration shared by guest and forwarding clients. */
public final class Ports {
  private Ports() {}

  public static int hook() {
    int port = Integer.parseInt(System.getenv().getOrDefault("AGENT_SANDBOX_HOOK_PORT", "9000"));
    if (port < 1 || port > 65535) throw new IllegalArgumentException("Invalid hook port");
    return port;
  }

  public static int agent() {
    int port = Integer.parseInt(System.getenv().getOrDefault("AGENT_SANDBOX_AGENT_PORT", "8080"));
    if (port < 1 || port > 65535 || port == hook())
      throw new IllegalArgumentException("Invalid agent port");
    return port;
  }
}
