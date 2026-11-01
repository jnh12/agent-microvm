package io.github.jnh12.vmnest;

import java.time.Duration;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.*;

final class Retry {
  static <T> T call(Supplier<T> action, Predicate<RuntimeException> retryable, Duration budget)
      throws InterruptedException {
    long start = System.nanoTime(), delay = 1000;
    while (true) {
      try {
        return action.get();
      } catch (RuntimeException error) {
        long remaining = budget.toNanos() - (System.nanoTime() - start);
        if (remaining <= 0 || !retryable.test(error)) throw error;
        long sleep =
            Math.min(
                remaining,
                Duration.ofMillis(
                        delay + ThreadLocalRandom.current().nextLong(Math.max(1, delay / 4)))
                    .toNanos());
        java.util.concurrent.TimeUnit.NANOSECONDS.sleep(sleep);
        delay = Math.min(10000, delay * 2);
      }
    }
  }
}
