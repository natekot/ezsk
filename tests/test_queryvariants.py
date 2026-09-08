from __future__ import annotations

from ezsk.queryvariants import split_excludes


# ---------- split_excludes ----------


def test_splits_single_exclusion_token():
    """no:word is pulled out of the query and returned as a term."""
    assert split_excludes("Talking Heads - Once in a Lifetime no:instrumental") == (
        "Talking Heads - Once in a Lifetime",
        ["instrumental"],
    )


def test_splits_quoted_phrase():
    """no:"two words" is one exclusion term, not two."""
    assert split_excludes('Tom Tom Club - Genius of Love no:"radio edit"') == (
        "Tom Tom Club - Genius of Love",
        ["radio edit"],
    )


def test_splits_multiple_tokens():
    """Several exclusions in one query are all collected."""
    query = 'Artist - Song no:karaoke no:"radio edit" no:live'
    assert split_excludes(query) == ("Artist - Song", ["karaoke", "radio edit", "live"])


def test_token_is_case_insensitive_and_normalized():
    """NO:Instrumental parses, and the term is normalized for matching."""
    assert split_excludes("Artist - Song NO:Instrumental") == (
        "Artist - Song",
        ["instrumental"],
    )


def test_duplicate_terms_are_collapsed():
    """The same exclusion twice yields one term."""
    assert split_excludes("Artist - Song no:live no:LIVE") == ("Artist - Song", ["live"])


def test_token_mid_query_is_stripped_without_leaving_double_spaces():
    """Removing an inline token collapses the whitespace it leaves behind."""
    assert split_excludes("Artist - Song no:live extra") == (
        "Artist - Song extra",
        ["live"],
    )


def test_query_without_tokens_is_unchanged():
    """A plain query round-trips with no exclusions."""
    assert split_excludes("Talking Heads - Once in a Lifetime") == (
        "Talking Heads - Once in a Lifetime",
        [],
    )


def test_bare_trailing_no_colon_is_ignored():
    """A dangling 'no:' is not an empty exclusion that would reject everything."""
    assert split_excludes("Artist - Song no:") == ("Artist - Song no:", [])


# ---------- false positives ----------
# These are the shapes that ruled out a bare `-word` token. Each must pass
# through untouched -- a misparse here silently mangles a real query.


def test_space_hyphen_title_is_not_an_exclusion():
    """'artist -title' is a real shape in scraped lists; it must survive."""
    assert split_excludes("artist -title") == ("artist -title", [])


def test_hyphenated_artist_is_not_an_exclusion():
    assert split_excludes("Jay-Z - Song") == ("Jay-Z - Song", [])


def test_bang_in_band_name_is_not_an_exclusion():
    assert split_excludes("Wham! - Last Christmas") == ("Wham! - Last Christmas", [])


def test_colon_in_title_is_not_an_exclusion():
    """A colon alone is not the token -- it must be the literal 'no:'."""
    assert split_excludes("Sunday 1:15 - Some Song") == ("Sunday 1:15 - Some Song", [])


def test_word_ending_in_no_is_not_an_exclusion():
    """The token needs a word boundary before 'no:', not just the letters."""
    assert split_excludes("Piano:Works - Some Song") == ("Piano:Works - Some Song", [])
