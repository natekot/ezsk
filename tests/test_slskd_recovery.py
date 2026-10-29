"""What happens when slskd goes away mid-run.

On 2026-09-28 the managed slskd died of "Out of memory." partway through a
batch. ezsk had no idea: every remaining search hit "Connection refused" in
milliseconds and was written off, so the whole ~340-entry queue drained in
about eighty seconds. A search that can't reach slskd has learned nothing
about whether anyone has the track -- the run should bring slskd back, or
stop, never keep failing entries against a dead server.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest
import requests

from ezsk.normalize import query_hash

QUERY = "Talking Heads - Once in a Lifetime"
RESPONSES = [{
    "username": "peer1",
    "hasFreeUploadSlot": True,
    "uploadSpeed": 1_000_000,
    "queueLength": 0,
    "files": [{"filename": "music\\Talking Heads - Once in a Lifetime.mp3",
               "size": 1_000_000, "bitRate": 1000}],
}]
ANSWERED = {"state": "InProgress", "isComplete": False, "fileCount": 1, "responseCount": 1}


def _refused() -> requests.ConnectionError:
    return requests.ConnectionError(
        "HTTPConnectionPool(host='localhost', port=5030): Max retries exceeded "
        "(Caused by NewConnectionError: [Errno 61] Connection refused)"
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Client:
    """slskd that is unreachable until `up` is set."""

    def __init__(self, up: bool = False, dies_mid_search: bool = False):
        self.up = up
        self.dies_mid_search = dies_mid_search
        self.started: list[str] = []

    def _check(self) -> None:
        if not self.up:
            raise _refused()

    def start_search(self, query, timeout_ms=60000):
        self._check()
        self.started.append(query)
        return f"sid-{len(self.started)}"

    def search_state(self, search_id):
        if self.dies_mid_search:
            self.up = False
        self._check()
        return ANSWERED

    def stop_search(self, search_id):
        self._check()

    def get_search_responses(self, search_id):
        self._check()
        return RESPONSES

    def delete_search(self, search_id):
        self._check()

    def cleanup_searches(self, spare_active=True):
        return 0


@pytest.fixture
def config(tmp_path):
    from ezsk.config import Config

    out = tmp_path / "output"
    out.mkdir()
    return Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(out),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
        title_only_search=False,
    )


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("ezsk.download.time", c)
    return c


def _entry(config, query=QUERY) -> dict:
    with open(config.state_file) as f:
        return json.load(f).get(query_hash(query), {})


def test_search_restarts_a_dead_slskd_and_carries_on(config):
    """Connection refused -> restart slskd -> the search goes ahead."""
    from ezsk.download import search_song

    client = _Client(up=False)
    recovered: list[bool] = []

    def _recover(cfg):
        recovered.append(True)
        client.up = True
        return True

    with patch("ezsk.slskd_manager.recover", side_effect=_recover):
        candidates = search_song(QUERY, config, client)

    assert recovered == [True]
    assert candidates, "the search should have run once slskd was back"


def test_unrecoverable_slskd_raises_instead_of_failing_the_entry(config):
    """If slskd can't be brought back, the query must not be written off."""
    from ezsk.download import SlskdUnavailable, search_song

    with patch("ezsk.slskd_manager.recover", return_value=False):
        with pytest.raises(SlskdUnavailable):
            search_song(QUERY, config, _Client(up=False))

    entry = _entry(config)
    assert entry.get("status") != "failed"
    assert entry.get("attempts", 0) == 0


def test_slskd_dying_mid_search_is_not_recorded_as_no_results(config):
    """A poll that can't reach slskd is not an empty answer.

    Before, the poll loop swallowed the error and returned no responses, so
    the in-flight searches at the moment of the crash were recorded as
    "no results" and charged an attempt against the retry cap.
    """
    from ezsk.download import SlskdUnavailable, search_song

    client = _Client(up=True, dies_mid_search=True)
    with patch("ezsk.slskd_manager.recover", return_value=False):
        with pytest.raises(SlskdUnavailable):
            search_song(QUERY, config, client)

    entry = _entry(config)
    assert entry.get("error") != "no results"
    assert entry.get("attempts", 0) == 0


def test_run_batch_stops_when_slskd_is_unavailable(config):
    """The batch halts rather than draining the queue against a dead slskd."""
    from ezsk.download import SlskdUnavailable
    from ezsk.service import DownloadService

    queries = [f"Artist {i} - Track {i}" for i in range(40)]
    searched: list[str] = []
    lock = threading.Lock()

    def _search(query, cfg, client, cancelled=None):
        with lock:
            searched.append(query)
        raise SlskdUnavailable("slskd is not reachable")

    svc = DownloadService(config)
    with patch("ezsk.service.SlskdClient"), \
         patch("ezsk.service.generate_dashboard"), \
         patch("ezsk.service.search_song", side_effect=_search):
        completed, failed, _skipped = svc.run_batch(queries)

    assert len(searched) < 10, "the backlog must not be searched against a dead slskd"
    assert failed == 0


# -- slskd_manager.recover ---------------------------------------------------


class _Proc:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_recover_does_nothing_for_an_slskd_we_did_not_start(config, monkeypatch):
    from ezsk import slskd_manager

    monkeypatch.setattr(slskd_manager, "_process", None)
    with patch.object(slskd_manager, "start") as start:
        assert slskd_manager.recover(config) is False
    start.assert_not_called()


def test_recover_restarts_a_managed_slskd_that_exited(config, monkeypatch):
    from ezsk import slskd_manager

    monkeypatch.setattr(slskd_manager, "_process", _Proc(returncode=1))

    def _start(cfg, verbose=False):
        slskd_manager._process = _Proc(returncode=None)

    with patch.object(slskd_manager, "start", side_effect=_start) as start, \
         patch.object(slskd_manager, "wait_for_healthy") as healthy:
        assert slskd_manager.recover(config) is True
    start.assert_called_once()
    healthy.assert_called_once()


def test_recover_reports_failure_when_the_restart_fails(config, monkeypatch):
    from ezsk import slskd_manager

    monkeypatch.setattr(slskd_manager, "_process", _Proc(returncode=1))
    with patch.object(slskd_manager, "start"), \
         patch.object(slskd_manager, "wait_for_healthy",
                      side_effect=RuntimeError("slskd exited with code 1")):
        assert slskd_manager.recover(config) is False
