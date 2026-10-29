"""download_candidates names the saved file from its tags when the query can't.

Seen live: "tapper zukie visions of love" was saved as "Unknown - tapper zukie
visions of love.flac", so it looked like it had never been downloaded, and
re-adding it just said "Started 0 queries".
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

REMOTE = "Shared\\Mr Bongo Record Club, Vol. 8\\12 Tapper Zukie - Visions of Love.flac"


def _download(tmp_path, query, tags):
    from ezsk.config import Config
    from ezsk.download import download_candidates

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
    )
    config.strict_metadata_check = False

    src = tmp_path / "dl" / "12 Tapper Zukie - Visions of Love.flac"
    src.parent.mkdir()
    src.write_bytes(b"fLaC")

    client = MagicMock()
    client.wait_for_download.return_value = {"state": "Completed, Succeeded"}
    candidates = [{"username": "lachiendupape", "filename": REMOTE, "size": 4}]

    with patch("ezsk.download.SlskdClient", return_value=client), \
            patch("ezsk.download._find_downloaded_file", return_value=str(src)), \
            patch("ezsk.download.read_artist_title", return_value=tags):
        assert download_candidates(query, candidates, config)

    return sorted(os.listdir(tmp_path / "output")), config


def test_a_query_with_no_artist_is_saved_under_the_files_tags(tmp_path):
    from ezsk.normalize import query_hash
    from ezsk.state import load_state

    query = "tapper zukie visions of love"
    saved, config = _download(tmp_path, query, ("Tapper Zukie", "Visions of Love"))

    assert saved == ["Tapper Zukie - Visions of Love.flac"]
    entry = load_state(config.state_file)[query_hash(query)]
    assert entry["status"] == "completed"
    assert entry["local_path"] == str(tmp_path / "output" / "Tapper Zukie - Visions of Love.flac")


def test_an_untagged_file_still_falls_back_to_unknown(tmp_path):
    saved, _ = _download(tmp_path, "tapper zukie visions of love", None)

    assert saved == ["Unknown - tapper zukie visions of love.flac"]
