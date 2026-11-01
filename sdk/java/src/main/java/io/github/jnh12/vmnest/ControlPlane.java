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

  public RunMicrovmResponse runMicrovm(RunMicrovmRequest request) throws InterruptedException {
    return runMicrovm(request, Duration.ofSeconds(180));
  }

  public RunMicrovmResponse runMicrovm(RunMicrovmRequest request, Duration retryTimeout)
      throws InterruptedException {
    if (retryTimeout.isNegative())
      throw new IllegalArgumentException("Nonnegative retry timeout required");
    var stable =
        request.clientToken() == null
            ? request.toBuilder().clientToken(java.util.UUID.randomUUID().toString()).build()
            : request;
    return Retry.call(
        () -> aws.runMicrovm(stable),
        e -> {
          if (!(e instanceof LambdaMicrovmsException failure)) return false;
          String code =
              failure.awsErrorDetails() == null
                  ? failure.getClass().getSimpleName()
                  : failure.awsErrorDetails().errorCode();
          return java.util.Set.of(
                  "ResourceNotFoundException", "ThrottlingException", "TooManyRequestsException")
              .contains(code);
        },
        retryTimeout);
  }

  public GetMicrovmResponse waitUntilRunning(String id, Duration timeout, Duration interval)
      throws InterruptedException, TimeoutException {
    if (timeout.isNegative() || timeout.isZero() || interval.isNegative() || interval.isZero())
      throw new IllegalArgumentException("Positive wait durations required");
    long start = System.nanoTime();
    while (System.nanoTime() - start < timeout.toNanos()) {
      var info = aws.getMicrovm(r -> r.microvmIdentifier(id));
      String state =
          java.util.Objects.toString(info.stateAsString(), "").toUpperCase(java.util.Locale.ROOT);
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
