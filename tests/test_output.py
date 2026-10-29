"""Naming the saved file.

The pattern is "{artist} - {title}", with the artist and title taken from the
query. A query typed without the " - " separator ("tapper zukie visions of
love") has no artist, so it was filed as "Unknown - tapper zukie visions of
love.flac" -- 16 of them in the live library, all with correct tags.
"""
from __future__ import annotations

import os


def _name(query, tags=None, remote="music\\12 Tapper Zukie - Visions of Love.flac"):
    from ezsk.output import format_output_path

    return os.path.basename(
        format_output_path("/lib", "{artist} - {title}.{ext}", query, remote, tags=tags)
    )


def test_a_query_without_an_artist_is_named_from_the_files_tags():
    assert _name("tapper zukie visions of love", tags=("Tapper Zukie", "Visions of Love")) == (
        "Tapper Zukie - Visions of Love.flac"
    )


def test_a_query_with_an_artist_keeps_its_own_name():
    """The query is what the user asked for; tags only fill in what it lacks."""
    assert _name("Tapper Zukie - Visions Of Love", tags=("TAPPER ZUKIE", "visions")) == (
        "Tapper Zukie - Visions Of Love.flac"
    )


def test_without_tags_a_query_with_no_artist_is_still_filed_under_unknown():
    assert _name("tapper zukie visions of love") == "Unknown - tapper zukie visions of love.flac"


def test_tags_are_sanitized_like_any_other_name():
    """Tags carry characters a filename can't, e.g. "(Who?) Keeps Changing Your Mind?"."""
    assert _name("keeps changing your mind", tags=("South Street Player", "Who? Mind/Edit")) == (
        "South Street Player - Who_ Mind_Edit.flac"
    )
