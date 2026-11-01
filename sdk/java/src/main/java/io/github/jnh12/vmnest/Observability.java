package io.github.jnh12.vmnest;

import java.time.*;
import java.util.*;
import software.amazon.awssdk.services.cloudwatch.CloudWatchClient;
import software.amazon.awssdk.services.cloudwatch.model.*;
import software.amazon.awssdk.services.cloudwatchlogs.CloudWatchLogsClient;
import software.amazon.awssdk.services.cloudwatchlogs.model.*;

/** Caller owns CloudWatch clients. Empty metrics mean no datapoint, never a fabricated zero. */
public final class Observability {
  private final CloudWatchClient metrics;
  private final CloudWatchLogsClient logs;

  public Observability(CloudWatchClient metrics, CloudWatchLogsClient logs) {
    this.metrics = metrics;
    this.logs = logs;
  }

  public Map<String, OptionalDouble> metrics(String id, Duration lookback) {
    if (lookback.isNegative() || lookback.isZero())
      throw new IllegalArgumentException("Positive lookback required");
    Instant end = Instant.now();
    var out = new LinkedHashMap<String, OptionalDouble>();
    for (String name : List.of("CPUUtilization", "MemoryUtilization")) {
      var points =
          metrics
              .getMetricStatistics(
                  r ->
                      r.namespace("AWS/LambdaMicroVMs")
                          .metricName(name)
                          .dimensions(d -> d.name("MicrovmId").value(id))
                          .startTime(end.minus(lookback))
                          .endTime(end)
                          .period(60)
                          .statistics(Statistic.AVERAGE))
              .datapoints();
      var latest =
          points.stream()
              .filter(p -> p.timestamp() != null && p.average() != null)
              .max(Comparator.comparing(Datapoint::timestamp));
      out.put(
          name,
          latest.isPresent()
              ? OptionalDouble.of(
                  new java.math.BigDecimal(latest.get().average())
                      .setScale(2, java.math.RoundingMode.HALF_EVEN)
                      .doubleValue())
              : OptionalDouble.empty());
    }
    return out;
  }

  public record MetricResult(Map<String, OptionalDouble> metrics, String error) {
    public MetricResult {
      metrics = Map.copyOf(metrics);
    }
  }

  public record MetricSample(int sample, Instant at, Map<String, OptionalDouble> metrics) {
    public MetricSample {
      metrics = Map.copyOf(metrics);
    }
  }

  public Map<String, MetricResult> metricsAll(Collection<String> ids, Duration lookback) {
    var out = new LinkedHashMap<String, MetricResult>();
    for (var id : ids) {
      try {
        out.put(id, new MetricResult(metrics(id, lookback), null));
      } catch (RuntimeException e) {
        out.put(id, new MetricResult(Map.of(), e.getMessage()));
      }
    }
    return out;
  }

  public List<MetricSample> sampleMetrics(String id, int samples, Duration interval)
      throws InterruptedException {
    int count = Math.max(1, Math.min(60, samples));
    var out = new ArrayList<MetricSample>();
    for (int i = 0; i < count; i++) {
      if (i > 0) Thread.sleep(Math.max(0, interval.toMillis()));
      var data = metrics(id, Duration.ofMinutes(5));
      out.add(new MetricSample(i, Instant.now(), data));
    }
    return List.copyOf(out);
  }

  public List<FilteredLogEvent> readLogs(String group, int tail, Instant since, String contains) {
    if (tail < 1 || tail > 10000) throw new IllegalArgumentException("tail must be 1–10000");
    var request = FilterLogEventsRequest.builder().logGroupName(group);
    if (since != null) request.startTime(since.toEpochMilli());
    var result = new ArrayDeque<FilteredLogEvent>();
    for (var page : logs.filterLogEventsPaginator(request.build()))
      for (var event : page.events()) {
        if (contains == null || Objects.toString(event.message(), "").contains(contains)) {
          result.addLast(event);
          if (result.size() > tail) result.removeFirst();
        }
      }
    return List.copyOf(result);
  }

  /** Checkpoint for one log group; retains a one-minute overlap for delayed ingestion. */
  public record LogCursor(
      String group,
      long startTime,
      String nextToken,
      long latestTimestamp,
      Map<String, Long> seen) {
    public LogCursor {
      seen = Map.copyOf(seen);
    }
  }

  public record LogBatch(List<FilteredLogEvent> events, LogCursor cursor) {
    public LogBatch {
      events = List.copyOf(events);
    }
  }

  public LogBatch pollLogs(String group, LogCursor cursor, Duration follow)
      throws InterruptedException {
    if (follow.isNegative())
      throw new IllegalArgumentException("Nonnegative follow duration required");
    if (cursor != null && !group.equals(cursor.group()))
      throw new IllegalArgumentException("Cursor belongs to a different log group");
    long start = System.nanoTime();
    long since =
        cursor == null ? Instant.now().minusSeconds(60).toEpochMilli() : cursor.startTime();
    long latest = cursor == null ? since : cursor.latestTimestamp();
    String token = cursor == null ? null : cursor.nextToken();
    var seen = new HashMap<String, Long>(cursor == null ? Map.of() : cursor.seen());
    do {
      var request =
          FilterLogEventsRequest.builder()
              .logGroupName(group)
              .limit(1000)
              .startTime(since)
              .nextToken(token);
      var result = logs.filterLogEvents(request.build());
      var events = new ArrayList<FilteredLogEvent>();
      for (var event : result.events()) {
        long timestamp = event.timestamp();
        latest = Math.max(latest, timestamp);
        if (seen.putIfAbsent(event.eventId(), timestamp) == null) events.add(event);
      }
      token = result.nextToken();
      if (token == null) {
        // Advance only after all pages; keep overlap and IDs for late arrivals and equal
        // timestamps.
        since = Math.max(since, latest - 60_000);
        long cutoff = since;
        seen.values().removeIf(timestamp -> timestamp < cutoff);
      }
      var checkpoint = new LogCursor(group, since, token, latest, seen);
      if (!events.isEmpty() || System.nanoTime() - start >= follow.toNanos())
        return new LogBatch(events, checkpoint);
      if (token == null) Thread.sleep(Math.min(500, Math.max(1, follow.toMillis())));
    } while (true);
  }
}
