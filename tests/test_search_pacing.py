"""How _smart_search spends its time, and the cap on how many run at once.

slskd forwards only two searches at a time to the Soulseek server and queues
the rest internally. A queued search has produced nothing for reasons that
have nothing to do with availability, so timing it from submission is what
turns "slskd was busy" into a recorded miss against the query's retry cap.
"""
from __future__ import annotations

import itertools
import threading
import time

import pytest

from ezsk.client import SearchGate, active_search_count
from ezsk.config import Config
from ezsk.download import _smart_search


QUEUED = {"state": "Requested", "isComplete": False, "fileCount": 0, "responseCount": 0}
SILENT = {"state": "InProgress", "isComplete": False, "fileCount": 0, "responseCount": 0}
ANSWERED = {"state": "InProgress", "isComplete": False, "fileCount": 4, "responseCount": 2}
RESPONSES = [{"username": "u", "files": [{"filename": "a.flac", "size": 1}]}]


class _Clock:
    """Stand-in for the time module: sleeping just advances the clock."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeClient:
    """Replays a scripted sequence of slskd search states."""

    def __init__(self, states, responses=None):
        self.states = list(states)
        self.responses = responses or []
        self.stopped: list[str] = []
        self.deleted: list[str] = []
        self.state_calls = 0
        self.cleanups = 0

    def start_search(self, query, timeout_ms=60000):
        return "sid-1"

    def cleanup_searches(self, spare_active=True):
        self.cleanups += 1
        return 0

    def search_state(self, search_id):
        state = self.states[min(self.state_calls, len(self.states) - 1)]
        self.state_calls += 1
        return state

    def stop_search(self, search_id):
        self.stopped.append(search_id)

    def get_search_responses(self, search_id):
        return self.responses

    def delete_search(self, search_id):
        self.deleted.append(search_id)


@pytest.fixture
def config(tmp_path):
    return Config(
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "ezsk.log"),
        output_dir=str(tmp_path),
    )


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("ezsk.download.time", c)
    return c


def test_time_spent_queued_inside_slskd_is_not_counted_against_the_search(config, clock):
    """A search slskd hasn't sent yet keeps its full budget once it goes out.

    Sixty seconds queued then an answer: timed from submission that is past
    the old deadline, so the responses were thrown away and the track recorded
    as a miss.
    """
    client = _FakeClient([QUEUED] * 30 + [ANSWERED], responses=RESPONSES)

    start = clock.now
    _search_id, responses = _smart_search(client, "artist title", config)

    assert clock.now - start > config.search_timeout, "test should cover the old deadline"
    assert responses == RESPONSES
    assert client.stopped == ["sid-1"]
    assert client.deleted == ["sid-1"]


def test_a_search_nobody_answers_is_abandoned_early(config, clock):
    """No responses at all means stop at no_result_timeout, not the full budget.

    search_song tries three query variants, so the full budget three times
    over is most of the wall-clock cost of a track nobody is sharing.
    """
    client = _FakeClient([SILENT] * 100, responses=[])

    start = clock.now
    _search_id, responses = _smart_search(client, "artist title", config)
    spent = clock.now - start

    assert responses == []
    assert spent >= config.search_no_result_timeout
    assert spent < config.search_timeout
    assert client.stopped == ["sid-1"]


def test_a_search_that_never_leaves_the_queue_still_terminates(config, clock):
    """The hard deadline runs from submission so a worker can't be pinned."""
    client = _FakeClient([QUEUED] * 10_000, responses=[])

    start = clock.now
    _smart_search(client, "artist title", config)

    # The hard deadline, plus the few seconds of post-stop response polling.
    assert clock.now - start <= config.search_timeout * 2 + 30 + 5
    assert client.deleted == ["sid-1"]


def test_a_dispatched_search_still_gets_its_full_budget(config, clock):
    """Answers arriving late are worth waiting for; silence is not."""
    # Responses arrive, then nothing more for a long while: the early stop
    # needs min_wait, and the no-result bail must not fire once someone spoke.
    client = _FakeClient([ANSWERED] * 100, responses=RESPONSES)

    start = clock.now
    _search_id, responses = _smart_search(client, "artist title", config)
    spent = clock.now - start

    assert responses == RESPONSES
    assert spent >= config.search_min_wait


def test_cancellation_interrupts_a_search_in_flight(config, clock):
    from ezsk.download import SearchCancelled

    cancelled = threading.Event()
    cancelled.set()
    client = _FakeClient([SILENT] * 100)

    with pytest.raises(SearchCancelled):
        _smart_search(client, "artist title", config, cancelled)


def test_the_gate_never_lets_more_than_its_limit_through():
    gate = SearchGate(2)
    peak = 0
    live = 0
    seen = threading.Lock()
    done = threading.Event()

    def worker() -> None:
        nonlocal peak, live
        for _ in range(20):
            with gate.hold():
                with seen:
                    live += 1
                    peak = max(peak, live)
                done.wait(0.001)
                with seen:
                    live -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 2


def test_smart_search_holds_the_gate_across_the_whole_search(config, monkeypatch):
    """Concurrent callers never have more searches open on slskd than the cap.

    The gate is process-wide on purpose: the batch's search workers, the
    per-query tasks POST /api/run spawns, retry and replace all funnel through
    _smart_search, and slskd's limit is per instance, not per caller.
    """
    from ezsk import client as client_mod

    config.search_concurrency = 2
    config.search_min_wait = 0  # early-stop on the first poll; keep the test quick
    ids = itertools.count()
    peak = 0
    sample_lock = threading.Lock()

    class _GateClient(_FakeClient):
        def start_search(self, query, timeout_ms=60000):
            sid = f"sid-{next(ids)}"
            with client_mod._active_lock:
                client_mod._active_searches.add(sid)
            return sid

        def delete_search(self, search_id):
            with client_mod._active_lock:
                client_mod._active_searches.discard(search_id)

        def search_state(self, search_id):
            nonlocal peak
            with sample_lock:
                peak = max(peak, active_search_count())
            time.sleep(0.02)  # real, so the holders actually overlap
            return super().search_state(search_id)

    def run() -> None:
        _smart_search(_GateClient([ANSWERED] * 3, responses=RESPONSES), "a b", config)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 2, f"expected the gate to admit exactly 2 at a time, saw {peak}"
    assert active_search_count() == 0


class _ThrottlingClient(_FakeClient):
    """Answers 429 for the first `refusals` search starts, like slskd under load."""

    def __init__(self, refusals, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.refusals = refusals
        self.starts = 0

    def start_search(self, query, timeout_ms=60000):
        self.starts += 1
        if self.starts <= self.refusals:
            raise RuntimeError(
                "429 Client Error: Too Many Requests for url: "
                "http://localhost:5030/api/v0/searches"
            )
        return "sid-1"


def test_a_rate_limited_search_is_retried_not_written_off(config, clock):
    """slskd's own 429 must not be recorded as "nobody has this track".

    Seen live: slskd rate-limits its search endpoint independently of the
    Soulseek server and answers 429 under a burst even at two concurrent
    searches. The search comes back in milliseconds, so the track is written
    off with no sign anything went wrong.
    """
    client = _ThrottlingClient(2, [ANSWERED] * 5, responses=RESPONSES)

    _search_id, responses = _smart_search(client, "artist title", config)

    assert client.starts == 3, "should have backed off and retried twice"
    assert responses == RESPONSES


def test_a_search_refused_past_the_backoff_budget_still_raises(config, clock):
    """Persistent throttling surfaces rather than looping forever."""
    client = _ThrottlingClient(99, [ANSWERED] * 5, responses=RESPONSES)

    with pytest.raises(RuntimeError, match="429"):
        _smart_search(client, "artist title", config)

    assert client.starts == 4  # the initial try plus the three backoffs


class _RefusingClient(_FakeClient):
    """Answers every search start with slskd's 400, reason included."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.starts = 0

    def start_search(self, query, timeout_ms=60000):
        self.starts += 1
        raise RuntimeError(
            "400 Client Error: Bad Request for url: "
            "http://localhost:5030/api/v0/searches: Search query must contain "
            "at least one non-exclusion term with length greater than 1"
        )


def test_a_400_that_survives_a_purge_is_a_rejection(config, clock):
    """A 400 gets one purge-and-retry; a second one means slskd refuses the text.

    Every 400 used to be read as "search history full", and a repeat raised a
    plain error that search_song recorded as transient -- so a query slskd
    will never accept was retried every five minutes forever, uncounted.
    """
    from ezsk.client import SearchRejected

    client = _RefusingClient([ANSWERED] * 5, responses=RESPONSES)

    with pytest.raises(SearchRejected, match="non-exclusion term"):
        _smart_search(client, "artist title", config)

    assert client.starts == 2
    assert client.cleanups == 1


def test_a_rejection_is_never_retried_whatever_its_text(config, clock):
    """A SearchRejected can't be fixed by purging slskd's searches.

    The retry handling matches on substrings like "400", so a rejection
    carrying slskd's status text must be passed through by type, not text.
    """
    from ezsk.client import SearchRejected

    class _Rejecting(_RefusingClient):
        def start_search(self, query, timeout_ms=60000):
            self.starts += 1
            raise SearchRejected("400 Client Error: Bad Request: query refused")

    client = _Rejecting([ANSWERED] * 5, responses=RESPONSES)

    with pytest.raises(SearchRejected):
        _smart_search(client, "i d l e", config)

    assert client.starts == 1
    assert client.cleanups == 0
