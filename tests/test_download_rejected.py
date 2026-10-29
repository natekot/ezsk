"""search_song when slskd refuses the search text itself.

Seen live: titles like "I.D.L.E." normalize to one-letter words ("i d l e"),
which slskd refuses with a 400. search_song gave up after the title-only
search without trying the full artist+title one, and recorded the failure as
transient -- so the query never counted an attempt and was searched again
every five minutes for days.
"""
from __future__ import annotations

from unittest.mock import patch

from ezsk.client import SearchRejected, searchable

RESPONSES = [{
    "username": "peer1",
    "hasFreeUploadSlot": True,
    "uploadSpeed": 1_000_000,
    "queueLength": 0,
    "files": [
        {"filename": "music\\Model 500 - I.D.L.E..mp3", "size": 10_000_000, "bitRate": 320},
    ],
}]


def _make_config(tmp_path):
    from ezsk.config import Config

    return Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
    )


def _like_slskd(client, text, config, cancelled=None):
    """Stands in for _smart_search: refuses what slskd refuses, else answers."""
    if not searchable(text):
        raise SearchRejected(
            f"slskd needs a word longer than one character to search, got {text!r}"
        )
    return "sid", RESPONSES


def _entry(config, query):
    from ezsk.normalize import query_hash
    from ezsk.state import load_state

    return load_state(config.state_file)[query_hash(query)]


def test_a_title_slskd_refuses_falls_back_to_the_full_search(tmp_path):
    from ezsk.download import search_song

    config = _make_config(tmp_path)
    with patch("ezsk.download._smart_search", side_effect=_like_slskd) as ss:
        candidates = search_song("Model 500 - I.D.L.E.", config, client=None)

    sent = [call.args[1] for call in ss.call_args_list]
    assert "model 500 i d l e" in sent, sent
    assert candidates, "the full search should have found the track"


def test_a_query_slskd_can_never_search_counts_an_attempt(tmp_path):
    from ezsk.download import search_song

    config = _make_config(tmp_path)
    with patch("ezsk.download._smart_search", side_effect=_like_slskd):
        assert search_song("A - B", config, client=None) is None

    entry = _entry(config, "A - B")
    assert entry["status"] == "failed"
    assert entry["attempts"] == 1
    assert "longer than one character" in entry["error"]


def test_an_infrastructure_error_still_does_not_count(tmp_path):
    """Guard: only a refusal of the text counts; a 409 or the like still doesn't."""
    from ezsk.download import search_song

    config = _make_config(tmp_path)
    boom = RuntimeError("409 Client Error: Conflict for url: http://localhost:5030/api/v0/searches")
    with patch("ezsk.download._smart_search", side_effect=boom):
        assert search_song("Model 500 - I.D.L.E.", config, client=None) is None

    entry = _entry(config, "Model 500 - I.D.L.E.")
    assert entry["status"] == "failed"
    assert entry.get("attempts", 0) == 0
