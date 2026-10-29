from __future__ import annotations


def _config(tmp_path):
    from ezsk.config import Config
    c = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
    )
    c.watch_dir = str(tmp_path / "watch")
    c.anthropic_api_key = "test-anthropic-key"
    return c


def test_autorun_enabled_with_watch_dir_and_key(tmp_path):
    """A fully configured install auto-processes the watch dir."""
    from ezsk.web.app import autorun_enabled
    assert autorun_enabled(_config(tmp_path)) is True


def test_autorun_disabled_without_watch_dir(tmp_path):
    """No watch dir means nothing to auto-process."""
    from ezsk.web.app import autorun_enabled
    c = _config(tmp_path)
    c.watch_dir = ""
    assert autorun_enabled(c) is False


def test_autorun_disabled_without_api_key(tmp_path):
    """Parsing needs an Anthropic key, so auto-run stays off without one."""
    from ezsk.web.app import autorun_enabled
    c = _config(tmp_path)
    c.anthropic_api_key = ""
    assert autorun_enabled(c) is False


def test_autorun_disabled_by_no_autorun_flag(tmp_path):
    """no_autorun turns the download loop off on an otherwise complete config."""
    from ezsk.web.app import autorun_enabled
    c = _config(tmp_path)
    c.no_autorun = True
    assert autorun_enabled(c) is False


def test_apply_env_sets_no_autorun(monkeypatch, tmp_path):
    """EZSK_NO_AUTORUN=1 disables auto-run without touching watch_dir."""
    monkeypatch.setenv("EZSK_NO_AUTORUN", "1")
    c = _config(tmp_path)
    c.apply_env()
    assert c.no_autorun is True
    assert c.watch_dir == str(tmp_path / "watch")


def test_no_autorun_defaults_off(monkeypatch, tmp_path):
    """Without the env var, auto-run is left enabled."""
    monkeypatch.delenv("EZSK_NO_AUTORUN", raising=False)
    c = _config(tmp_path)
    c.apply_env()
    assert c.no_autorun is False


def _ago(hours: float) -> str:
    """A last_attempt stamp `hours` in the past, in state.json's format."""
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - hours * 3600))


def _state(**entries):
    """Build a state dict from query -> entry fields."""
    from ezsk.normalize import query_hash
    return {query_hash(q): {"query": q, **fields} for q, fields in entries.items()}


def test_a_miss_waits_out_its_retry_cooldown_wherever_it_comes_from(tmp_path):
    """A failed query is not searched again until its 24h wait is up.

    Seen live: run_batch writes misses to queued.txt, and the loop re-read
    that file (and the watch dir) every five minutes, so a track nobody had
    burned all five attempts in about half an hour.
    """
    from ezsk.web.app import gather_autorun_queries

    q = "Tnertle - Andreas"
    state = _state(**{q: {"status": "failed", "attempts": 1, "last_attempt": _ago(1)}})

    assert gather_autorun_queries([q], state, {q}, []) == []


def test_a_miss_whose_cooldown_is_up_is_retried(tmp_path):
    from ezsk.web.app import gather_autorun_queries

    q = "Tnertle - Andreas"
    state = _state(**{q: {"status": "failed", "attempts": 1, "last_attempt": _ago(25)}})

    assert gather_autorun_queries([], state, set(), [q]) == [q]


def test_finished_queries_are_left_out(tmp_path):
    """Completed and exhausted queries don't reach run_batch at all.

    They used to, from the watch dir, and run_batch logged a "Giving up"
    line for each one on every pass -- 117k lines in a week.
    """
    from ezsk.web.app import gather_autorun_queries

    state = _state(**{
        "Done - Track": {"status": "completed"},
        "Dead - Track": {"status": "failed", "attempts": 5, "last_attempt": _ago(48)},
    })

    assert gather_autorun_queries(["Done - Track", "Dead - Track"], state, set(), []) == []


def test_new_and_orphaned_queued_work_is_picked_up(tmp_path):
    from ezsk.web.app import gather_autorun_queries

    state = _state(**{"Orphan - Track": {"status": "queued"}})

    got = gather_autorun_queries(["New - Track"], state, {"Listed - Track"}, [])

    assert got == ["New - Track", "Orphan - Track", "Listed - Track"]
