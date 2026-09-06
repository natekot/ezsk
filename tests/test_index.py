from __future__ import annotations

import json
from pathlib import Path

import pytest

from ezsk import index as index_mod
from ezsk.index import (
    INDEX_VERSION,
    build_index,
    check_queries,
    find_in_index,
    index_add,
    index_remove,
    load_index,
    save_index,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_build_index_walks_subdirectories(tmp_path):
    _touch(tmp_path / "Daft Punk - Around the World.flac")
    _touch(tmp_path / "Artist" / "Album" / "Artist - Song.mp3")
    _touch(tmp_path / "Deeply" / "Nested" / "Folder" / "Other Artist - Other Song.flac")

    index = build_index(str(tmp_path))

    paths = set(index.values())
    assert str(tmp_path / "Daft Punk - Around the World.flac") in paths
    assert str(tmp_path / "Artist" / "Album" / "Artist - Song.mp3") in paths
    assert (
        str(tmp_path / "Deeply" / "Nested" / "Folder" / "Other Artist - Other Song.flac")
        in paths
    )


def test_build_index_skips_internal_files(tmp_path):
    _touch(tmp_path / "Real Artist - Real Song.flac")
    # Synthetic non-audio files we should never include
    _touch(tmp_path / ".ezsk_index.json")
    _touch(tmp_path / "queued.txt")
    _touch(tmp_path / "dead.txt")
    _touch(tmp_path / "notes.md")

    index = build_index(str(tmp_path))

    assert len(index) == 1
    assert next(iter(index.values())).endswith("Real Artist - Real Song.flac")


def test_build_index_skips_non_audio_extensions(tmp_path):
    _touch(tmp_path / "Album cover.jpg")
    _touch(tmp_path / "liner-notes.pdf")
    _touch(tmp_path / "Real Artist - Track.flac")

    index = build_index(str(tmp_path))

    assert len(index) == 1


def test_build_index_uses_tag_fallback_when_filename_lacks_separator(
    tmp_path, monkeypatch,
):
    """Files whose stem has no ' - ' get a tag-derived secondary key."""
    audio_path = tmp_path / "Artist" / "Album" / "01.flac"
    _touch(audio_path)

    def fake_tag_key_for(path: str) -> str | None:
        if path == str(audio_path):
            return index_mod._normalize_for_index("Tagged Artist Tagged Title")
        return None

    monkeypatch.setattr(index_mod, "_tag_key_for", fake_tag_key_for)

    index = build_index(str(tmp_path))

    # Tag-derived key is present
    assert index_mod._normalize_for_index("Tagged Artist Tagged Title") in index
    # Filename-derived key is also present
    assert "01" in index
    # Both keys point to the same file
    assert index[index_mod._normalize_for_index("Tagged Artist Tagged Title")] == str(audio_path)


def test_build_index_skips_tag_lookup_when_filename_has_separator(
    tmp_path, monkeypatch,
):
    """We don't pay the mutagen open cost for ezsk-named files."""
    _touch(tmp_path / "Artist - Title.flac")
    calls: list[str] = []

    def fake_tag_key_for(path: str) -> str | None:
        calls.append(path)
        return None

    monkeypatch.setattr(index_mod, "_tag_key_for", fake_tag_key_for)

    build_index(str(tmp_path))

    assert calls == []  # never called, separator already in stem


def test_find_in_index_matches_via_tag_key(tmp_path, monkeypatch):
    audio_path = tmp_path / "01.flac"
    _touch(audio_path)
    monkeypatch.setattr(
        index_mod, "_tag_key_for",
        lambda p: index_mod._normalize_for_index("Tagged Artist Tagged Title")
        if p == str(audio_path) else None,
    )

    index = build_index(str(tmp_path))

    # The query "Tagged Artist - Tagged Title" should find the file via tag-key,
    # even though the filename "01" tells us nothing.
    result = find_in_index(index, "Tagged Artist - Tagged Title")
    assert result == str(audio_path)


def test_find_in_index_short_query_returns_none(tmp_path):
    _touch(tmp_path / "Some - Song.flac")
    index = build_index(str(tmp_path))
    # Single-token query is below MIN_TOKENS guard
    assert find_in_index(index, "Song") is None


def test_check_queries_partitions_have_and_missing(tmp_path):
    _touch(tmp_path / "Daft Punk - Around the World.flac")
    _touch(tmp_path / "Justice" / "Justice - D.A.N.C.E..flac")
    index = build_index(str(tmp_path))

    queries = [
        "Daft Punk - Around the World",
        "Justice - D.A.N.C.E.",
        "Some Other - Track Not Owned",
    ]

    have, missing = check_queries(index, queries)

    have_queries = {q for q, _ in have}
    assert "Daft Punk - Around the World" in have_queries
    assert "Justice - D.A.N.C.E." in have_queries
    assert missing == ["Some Other - Track Not Owned"]


def test_load_index_rebuilds_on_version_mismatch(tmp_path):
    output_dir = tmp_path
    _touch(output_dir / "Real Artist - Track.flac")
    # Write a stale v1-style index that points at a non-existent file
    old = {"version": 1, "entries": {"old key": "/does/not/exist.flac"}}
    (output_dir / ".ezsk_index.json").write_text(json.dumps(old))

    index = load_index(str(output_dir))

    # Rebuild path picked up the real file, not the stale entry
    assert any(p.endswith("Real Artist - Track.flac") for p in index.values())
    assert "old key" not in index


def test_save_and_load_round_trip_keeps_v2(tmp_path):
    output_dir = tmp_path
    output_dir.mkdir(exist_ok=True)
    src = {"a b": "/some/path/a - b.flac"}
    save_index(str(output_dir), src)

    raw = json.loads((output_dir / ".ezsk_index.json").read_text())
    assert raw["version"] == INDEX_VERSION
    assert raw["entries"] == src

    # Load returns the persisted entries verbatim when version matches.
    assert load_index(str(output_dir)) == src


def test_index_add_persists_new_entry(tmp_path):
    _touch(tmp_path / "Existing - Song.flac")
    idx = build_index(str(tmp_path))
    new_path = str(tmp_path / "Brand New - Track.flac")
    _touch(Path(new_path))

    index_add(idx, str(tmp_path), "Brand New - Track", new_path)

    reloaded = load_index(str(tmp_path))
    assert any(v == new_path for v in reloaded.values())


def test_index_remove_drops_entry_and_persists(tmp_path):
    track = str(tmp_path / "Brand New - Track.flac")
    _touch(Path(track))
    idx = {}
    index_add(idx, str(tmp_path), "Brand New - Track", track)

    index_remove(idx, str(tmp_path), "Brand New - Track")

    # Gone from the in-memory index and the supplanted track no longer "have".
    assert not find_in_index(idx, "Brand New - Track")
    have, missing = check_queries(idx, ["Brand New - Track"])
    assert have == []
    assert missing == ["Brand New - Track"]
    # Persisted change survives a reload.
    assert load_index(str(tmp_path)) == idx


def test_index_remove_missing_key_is_noop(tmp_path):
    idx = {"a b": "/some/path.flac"}
    # No persisted file should be written when nothing was removed.
    index_remove(idx, str(tmp_path), "Nothing Here - Absent")
    assert idx == {"a b": "/some/path.flac"}
    assert not (tmp_path / ".ezsk_index.json").exists()


# ---------- stale entries (files that left the library) ----------


def test_find_in_index_skips_entry_whose_file_is_gone(tmp_path):
    """An index entry pointing at a deleted file must not count as a match.

    Files leave the library after they are indexed -- moved into a music
    collection, renamed, deleted. A hit on such an entry made search_song mark
    the query "completed" without ever downloading anything.
    """
    track = tmp_path / "Alaska - Accuse.flac"
    _touch(track)
    idx = build_index(str(tmp_path))
    assert find_in_index(idx, "Alaska - Accuse") == str(track)

    track.unlink()

    assert find_in_index(idx, "Alaska - Accuse") is None


def test_find_in_index_stale_entry_does_not_mask_a_live_one(tmp_path):
    """A missing file must not shadow a real copy of the same track.

    The scan has to continue past a stale hit rather than give up on it -- the
    same track often sits in the library under a different name.
    """
    gone = tmp_path / "Omar S;Diviniti - Games That We Play.flac"
    live = tmp_path / "Omar S - Games That We Play.flac"
    _touch(gone)
    _touch(live)
    gone.unlink()

    # Built by hand so the stale entry is iterated first, not by dict luck.
    idx = {
        "omar s diviniti games that we play": str(gone),
        "omar s games that we play": str(live),
    }

    assert find_in_index(idx, "Omar S - Games That We Play") == str(live)


def test_check_queries_treats_a_gone_file_as_missing(tmp_path):
    """The /api/files check endpoint must not claim we own a deleted file."""
    track = tmp_path / "J Lloyd - If I Fall Under.flac"
    _touch(track)
    idx = build_index(str(tmp_path))
    track.unlink()

    have, missing = check_queries(idx, ["J Lloyd - If I Fall Under"])

    assert have == []
    assert missing == ["J Lloyd - If I Fall Under"]


def test_find_in_index_does_not_mutate_the_index(tmp_path):
    """Lookups stay pure -- pruning here would wipe the index on an unmounted volume."""
    track = tmp_path / "Alaska - Accuse.flac"
    _touch(track)
    idx = build_index(str(tmp_path))
    track.unlink()
    before = dict(idx)

    find_in_index(idx, "Alaska - Accuse")

    assert idx == before
    assert not (tmp_path / ".ezsk_index.json").exists()
