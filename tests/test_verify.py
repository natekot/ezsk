from __future__ import annotations

import sys
import types

import pytest


class _FakeMutagenFile:
    """Dict-like stand-in for `mutagen.File(path, easy=True)` return value."""

    def __init__(self, tags: dict[str, list[str]]):
        self._tags = tags

    def get(self, key, default=None):
        return self._tags.get(key, default)


def _install_fake_mutagen(monkeypatch, tags: dict[str, list[str]] | None):
    """Replace ezsk.verify.mutagen with a stub whose File() returns our fake.

    Pass tags=None to simulate a file mutagen could not parse.
    """
    fake_module = types.ModuleType("mutagen")

    def fake_file(path, easy=False):
        if tags is None:
            return None
        return _FakeMutagenFile(tags)

    fake_module.File = fake_file  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mutagen", fake_module)


def test_metadata_match_rejects_doors_mismatch(monkeypatch):
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {
        "artist": ["The Doors"],
        "title": ["You're Lost Little Girl"],
    })
    err = check_metadata_match("/fake/path.flac", "Little Sister - You're The One")
    assert err is not None
    assert "metadata mismatch" in err
    assert "The Doors" in err


def test_metadata_match_accepts_legit_match(monkeypatch):
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {
        "artist": ["Little Sister"],
        "title": ["You're The One"],
    })
    assert check_metadata_match(
        "/fake/path.flac", "Little Sister - You're The One",
    ) is None


def test_metadata_match_skips_when_tags_missing(monkeypatch):
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {"artist": [""], "title": [""]})
    assert check_metadata_match("/fake/path.flac", "Artist - Title") is None


def test_metadata_match_skips_when_file_unreadable(monkeypatch):
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, None)
    assert check_metadata_match("/fake/path.flac", "Artist - Title") is None


def test_metadata_match_skips_when_query_has_no_separator(monkeypatch):
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {
        "artist": ["Somebody"], "title": ["Something"],
    })
    # No " - " → cannot split artist/title from query, skip
    assert check_metadata_match("/fake/path.flac", "title-only-query") is None


def test_metadata_match_va_accepts_title_only(monkeypatch):
    """Compilations: artist tag is the performer, not 'Various Artists'."""
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {
        "artist": ["Tony Grey"],  # actual performer, not VA
        "title": ["You Are The One"],
    })
    assert check_metadata_match(
        "/fake/path.flac", "Various Artists - You Are The One",
    ) is None


def test_metadata_match_va_rejects_title_mismatch(monkeypatch):
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {
        "artist": ["Some Performer"],
        "title": ["Completely Different Song"],
    })
    err = check_metadata_match(
        "/fake/path.flac", "Various Artists - You Are The One",
    )
    assert err is not None


def test_metadata_match_accepts_artist_title_swap(monkeypatch):
    """Cross-matching allows tag-side artist/title swaps."""
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {
        "artist": ["You're The One"],  # tagger put title in artist field
        "title": ["Little Sister"],    # and artist in title field
    })
    # nq_artist={little,sister}, nq_title={youre,one}; t_all contains all four
    assert check_metadata_match(
        "/fake/path.flac", "Little Sister - You're The One",
    ) is None


def test_metadata_match_rejects_when_only_artist_weak(monkeypatch):
    """Under new OR semantics, a strong title can't rescue a weak artist."""
    from ezsk.verify import check_metadata_match

    _install_fake_mutagen(monkeypatch, {
        "artist": ["Somebody Else"],           # no overlap with "Little Sister"
        "title": ["You're The One"],           # full overlap with title
    })
    err = check_metadata_match(
        "/fake/path.flac", "Little Sister - You're The One",
    )
    assert err is not None
    assert "metadata mismatch" in err


def test_metadata_match_graceful_when_mutagen_missing(monkeypatch):
    from ezsk.verify import check_metadata_match

    # Force the import inside check_metadata_match to fail
    monkeypatch.setitem(sys.modules, "mutagen", None)
    with pytest.MonkeyPatch.context() as _:
        # Use a builtins-level guard: set mutagen to None so `import mutagen` raises
        assert check_metadata_match("/fake/path.flac", "Artist - Title") is None
