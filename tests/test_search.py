from __future__ import annotations

from ezsk.search import (
    _artist_match,
    _title_coverage_ok,
    rank_results,
    score_file,
)


def _make_response(username: str, filename: str, **file_extras) -> dict:
    """Build a fake slskd user response with a single file."""
    file_entry = {"filename": filename, "size": 1_000_000, "bitRate": 1000}
    file_entry.update(file_extras)
    return {
        "username": username,
        "hasFreeUploadSlot": True,
        "uploadSpeed": 1_000_000,
        "queueLength": 0,
        "files": [file_entry],
    }


# ---------- _artist_match ----------


def test_artist_match_rejects_common_word_overlap():
    """Little Sister must not match The Doors' 'You're Lost Little Girl'."""
    path = "@@tdzlm\\myzik\\The Doors 2006\\Strange Days\\02 - Youre Lost Little Girl.flac"
    assert _artist_match(path, "Little Sister") == -1


def test_artist_match_accepts_exact_two_word_artist():
    path = "Little Sister\\Live\\01 - Youre The One.flac"
    assert _artist_match(path, "Little Sister") > 0


def test_artist_match_rejects_single_word_substring():
    """Prince should not match Princess."""
    assert _artist_match("01 - Princess Diana.mp3", "Prince") == -1


def test_artist_match_accepts_single_word_boundary():
    assert _artist_match("Prince - Kiss.flac", "Prince") > 0


def test_artist_match_tiered_threshold_accepts_75_percent():
    """4-word artist with 3/4 words in filename should pass (>=75%)."""
    path = "Red Hot Chili - Californication.flac"
    # "peppers" missing, but 3/4 = 75% satisfies the loose tier
    assert _artist_match(path, "Red Hot Chili Peppers") > 0


def test_artist_match_tiered_threshold_rejects_below_75():
    """4-word artist with only 2/4 words in filename should fail."""
    path = "Red Hot - Something Else.flac"
    assert _artist_match(path, "Red Hot Chili Peppers") == -1


def test_artist_match_stopword_only_artist_falls_back():
    """Artist 'The' has no significant words; the permissive fallback keeps it alive."""
    # At least one raw word must appear
    assert _artist_match("The Band - Song.flac", "The") > 0
    assert _artist_match("Band - Song.flac", "The") == -1


def test_artist_match_stopword_heavy_artist():
    """'The Who' → significant={'who'}; filename with 'who' as a token passes."""
    assert _artist_match("The Who - Baba ORiley.flac", "The Who") > 0


def test_artist_match_various_artists_skips_gate():
    """VA compilations should not be blocked by the artist gate."""
    path = "VA - Best of 2010\\12 - Real Artist - Song.flac"
    assert _artist_match(path, "Various Artists") == 0
    assert _artist_match(path, "VA") == 0


# ---------- title coverage gate ----------


def test_title_coverage_rejects_doors_mismatch():
    """Title 'Youre The One' vs filename 'youre lost little girl' → reject."""
    filename = "02 - Youre Lost Little Girl.flac"
    assert _title_coverage_ok(filename, "Little Sister - You're The One") is False


def test_title_coverage_accepts_matching_title():
    filename = "01 - Youre The One.flac"
    assert _title_coverage_ok(filename, "Little Sister - You're The One") is True


def test_title_coverage_strips_stopwords():
    """'You're The One' → significant={youre, one}; a filename with just 'one' is 50% → reject."""
    filename = "03 - One.flac"
    assert _title_coverage_ok(filename, "Little Sister - You're The One") is False


def test_title_coverage_allows_remix_filler_mismatch():
    """Filler words like 'remix' are dropped before the ratio is computed."""
    # query title: "Song (Extended Remix)" → significant={song, extended}
    filename = "Song (Extended Mix).flac"  # mix/remix both dropped
    # significant in query = {song, extended}; in filename we have song+extended → 2/2
    assert _title_coverage_ok(filename, "Artist - Song Extended Remix") is True


# ---------- rank_results end-to-end ----------


def test_rank_results_filters_out_doors_case():
    """The reported bug: Little Sister / You're The One must not return the Doors track."""
    bad_path = (
        "@@tdzlm\\myzik\\The Doors 2006\\Strange Days\\02 - Youre Lost Little Girl.flac"
    )
    responses = [_make_response("borisk", bad_path, bitDepth=16)]
    candidates = rank_results(
        responses, "Little Sister - You're The One",
        preferred_format="flac", min_bitrate=320, min_bit_depth=16,
        artist_filter="Little Sister",
    )
    assert candidates == []


def test_rank_results_accepts_legit_match():
    good_path = "Little Sister\\01 - Youre The One.flac"
    responses = [_make_response("goodseed", good_path, bitDepth=16)]
    candidates = rank_results(
        responses, "Little Sister - You're The One",
        preferred_format="flac", min_bitrate=320, min_bit_depth=16,
        artist_filter="Little Sister",
    )
    assert len(candidates) == 1
    assert candidates[0]["filename"] == good_path


def test_rank_results_va_compilation_passes():
    path = "Various Artists\\Lagos Disco Inferno\\02 Real Artist - Song Title.flac"
    responses = [_make_response("seeder", path, bitDepth=16)]
    candidates = rank_results(
        responses, "Various Artists - Song Title",
        preferred_format="flac", min_bitrate=320, min_bit_depth=16,
        artist_filter="Various Artists",
    )
    assert len(candidates) == 1


def test_rank_results_no_artist_filter_keeps_old_behavior():
    """Without artist_filter, the hard gates don't apply (full-search path)."""
    path = "random\\02 - something.mp3"
    responses = [_make_response("user", path, bitRate=320)]
    candidates = rank_results(
        responses, "Artist - Title",
        preferred_format="mp3", min_bitrate=320, min_bit_depth=16,
    )
    assert len(candidates) == 1


def test_score_file_rejects_non_audio_extension():
    assert score_file(
        {"filename": "cover.jpg"}, {}, "Artist - Title",
    ) == -1
