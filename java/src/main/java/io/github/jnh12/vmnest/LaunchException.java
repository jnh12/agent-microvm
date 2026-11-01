package io.github.jnh12.vmnest;

/** A VM was launched, but readiness/connection failed. Retain its ID for reconciliation. */
public final class LaunchException extends RuntimeException {
  private final String microvmId;

  public LaunchException(String id, Throwable cause) {
    super("VM " + id + " launched but did not become ready", cause);
    microvmId = id;
  }

  public String microvmId() {
    return microvmId;
  }
}
