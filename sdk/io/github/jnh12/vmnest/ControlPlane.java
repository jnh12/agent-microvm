package io.github.jnh12.vmnest;

import java.time.Duration;
import java.util.concurrent.TimeoutException;
import software.amazon.awssdk.services.lambdamicrovms.LambdaMicrovmsClient;
import software.amazon.awssdk.services.lambdamicrovms.model.*;

/** AWS's typed client exposes all lifecycle, image, token and pagination operations. */
public final class ControlPlane {
  private final LambdaMicrovmsClient aws;

  public ControlPlane(LambdaMicrovmsClient aws) {
    this.aws = aws;
  }

  /** Caller owns this client and closes it after all sandboxes are finished. */
  public LambdaMicrovmsClient aws() {
    return aws;
  }

  public GetMicrovmResponse waitUntilRunning(String id, Duration timeout, Duration interval)
      throws InterruptedException, TimeoutException {
    if (timeout.isNegative() || timeout.isZero() || interval.isNegative() || interval.isZero())
      throw new IllegalArgumentException("Positive wait durations required");
    long start = System.nanoTime();
    while (System.nanoTime() - start < timeout.toNanos()) {
      var info = aws.getMicrovm(r -> r.microvmIdentifier(id));
      String state = info.stateAsString();
      if ("RUNNING".equals(state)) return info;
      if ("FAILED".equals(state) || "TERMINATED".equals(state))
        throw new IllegalStateException("VM " + id + " entered " + state);
      long remaining = timeout.toNanos() - (System.nanoTime() - start);
      if (remaining > 0)
        java.util.concurrent.TimeUnit.NANOSECONDS.sleep(Math.min(interval.toNanos(), remaining));
    }
    throw new TimeoutException("VM " + id + " did not become RUNNING");
  }
}
