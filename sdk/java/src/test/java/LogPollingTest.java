import static org.junit.jupiter.api.Assertions.*;

import io.github.jnh12.vmnest.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.services.cloudwatchlogs.*;
import software.amazon.awssdk.services.cloudwatchlogs.model.*;

class LogPollingTest {
  static FilteredLogEvent event(String id, long time) {
    return FilteredLogEvent.builder().eventId(id).timestamp(time).message(id).build();
  }

  @Test
  void checkpointsSurvivePaginationDuplicatesEmptyPagesAndLongPauses() throws Exception {
    long t = Instant.now().minusSeconds(600).toEpochMilli();
    var requests = new ArrayList<FilterLogEventsRequest>();
    var responses =
        List.of(
            FilterLogEventsResponse.builder().events(event("a", t)).nextToken("page2").build(),
            FilterLogEventsResponse.builder().nextToken("page3").build(),
            FilterLogEventsResponse.builder().events(event("b", t)).build(),
            FilterLogEventsResponse.builder()
                .events(event("a", t), event("b", t), event("late", t - 1))
                .build(),
            FilterLogEventsResponse.builder().build(),
            FilterLogEventsResponse.builder().events(event("c", t + 120_000)).build());
    var calls = new AtomicInteger();
    var client =
        ExtendedTest.fake(
            CloudWatchLogsClient.class,
            (p, m, a) -> {
              requests.add((FilterLogEventsRequest) a[0]);
              return responses.get(calls.getAndIncrement());
            });
    var monitoring = new Observability(null, client);
    var cursor = new Observability.LogCursor("group", t - 60_000, null, t - 60_000, Map.of());
    var first = monitoring.pollLogs("group", cursor, Duration.ZERO);
    assertEquals("a", first.events().getFirst().eventId());
    var emptyPage = monitoring.pollLogs("group", first.cursor(), Duration.ZERO);
    assertTrue(emptyPage.events().isEmpty());
    assertEquals("page3", emptyPage.cursor().nextToken());
    var last = monitoring.pollLogs("group", emptyPage.cursor(), Duration.ZERO);
    assertEquals("b", last.events().getFirst().eventId());
    assertNull(last.cursor().nextToken());
    var late = monitoring.pollLogs("group", last.cursor(), Duration.ZERO);
    assertEquals(List.of("late"), late.events().stream().map(FilteredLogEvent::eventId).toList());
    var idle = monitoring.pollLogs("group", late.cursor(), Duration.ZERO);
    assertEquals(late.cursor(), idle.cursor());
    var resumed = monitoring.pollLogs("group", idle.cursor(), Duration.ZERO);
    assertEquals("c", resumed.events().getFirst().eventId());
    assertEquals(t - 60_000, requests.get(5).startTime());
    assertEquals(t + 60_000, resumed.cursor().startTime());
    assertEquals(Set.of("c"), resumed.cursor().seen().keySet());
    assertEquals("page2", requests.get(1).nextToken());
    assertEquals("page3", requests.get(2).nextToken());
    assertThrows(
        IllegalArgumentException.class,
        () -> monitoring.pollLogs("other", resumed.cursor(), Duration.ZERO));
  }
}
