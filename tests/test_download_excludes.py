from __future__ import annotations

from unittest.mock import patch

QUERY = "Talking Heads - Once in a Lifetime"

RESPONSES = [{
    "username": "peer1",
    "hasFreeUploadSlot": True,
    "uploadSpeed": 1_000_000,
    "queueLength": 0,
    "files": [
        {"filename": "music\\Talking Heads - Once in a Lifetime (Instrumental).mp3",
         "size": 1_000_000, "bitRate": 1000},
        {"filename": "music\\Talking Heads - Once in a Lifetime.mp3",
         "size": 1_000_000, "bitRate": 1000},
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


def _search(query, tmp_path):
    from ezsk.download import search_song

    config = _make_config(tmp_path)
    with patch("ezsk.download._smart_search", return_value=("sid", RESPONSES)):
        return search_song(query, config, client=None)


def test_search_song_without_exclusion_can_return_the_instrumental(tmp_path):
    """Baseline: the instrumental is a legitimate candidate today."""
    names = [c["filename"] for c in _search(QUERY, tmp_path)]
    assert any("Instrumental" in n for n in names)


def test_search_song_drops_the_instrumental_when_excluded(tmp_path):
    """no:instrumental removes it while leaving the real track."""
    names = [c["filename"] for c in _search(f"{QUERY} no:instrumental", tmp_path)]
    assert names, "the clean version must still be found"
    assert not any("Instrumental" in n for n in names)


def test_exclusion_token_is_stripped_before_scoring(tmp_path):
    """The token must not survive into the query used for title coverage.

    score_file feeds the query through _title_coverage_ok, which requires 60%
    of the title's significant words to appear in the filename. Leave
    'instrumental' in the query and it becomes a *required* word -- rejecting
    exactly the results the user asked for.
    """
    candidates = _search(f"{QUERY} no:instrumental", tmp_path)
    assert [c["filename"] for c in candidates] == [
        "music\\Talking Heads - Once in a Lifetime.mp3"
    ]


def test_search_query_sent_to_slskd_has_no_token(tmp_path):
    """The token is ezsk syntax; slskd must never see it."""
    from ezsk.download import search_song

    config = _make_config(tmp_path)
    with patch("ezsk.download._smart_search", return_value=("sid", RESPONSES)) as ss:
        search_song(f"{QUERY} no:instrumental", config, client=None)
    sent = [call.args[1] for call in ss.call_args_list]
    assert sent, "expected at least one search"
    assert not any("no" == w for s in sent for w in s.split()), sent
    assert not any("instrumental" in s for s in sent), sent


def test_state_keeps_the_raw_query_the_user_typed(tmp_path):
    """State/dashboard identity stays the raw line, so resume round-trips."""
    from ezsk.state import load_state

    raw = f"{QUERY} no:instrumental"
    config = _make_config(tmp_path)
    with patch("ezsk.download._smart_search", return_value=("sid", RESPONSES)):
        _search(raw, tmp_path)
    state = load_state(config.state_file)
    assert [e["query"] for e in state.values()] == [raw]
