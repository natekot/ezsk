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


# ---------- exclusion terms ----------


def test_score_file_rejects_excluded_word_in_filename():
    """A term typed as no:instrumental drops the result outright."""
    assert score_file(
        {"filename": "Artist - Song (Instrumental).mp3", "bitRate": 1000},
        {}, "Artist - Song", exclude_words=["instrumental"],
    ) == -1


def test_score_file_rejects_excluded_word_in_parent_folder():
    """Junk is often only in the folder name, not the file name."""
    assert score_file(
        {"filename": "@@abc\\Karaoke Hits 1998\\Artist - Song.mp3", "bitRate": 1000},
        {}, "Artist - Song", exclude_words=["karaoke"],
    ) == -1


def test_score_file_matches_excluded_phrase():
    """no:"radio edit" excludes the phrase, not either word alone."""
    file = {"filename": "Artist - Song (Radio Edit).mp3", "bitRate": 1000}
    assert score_file(file, {}, "Artist - Song", exclude_words=["radio edit"]) == -1
    assert score_file(file, {}, "Artist - Song", exclude_words=["studio edit"]) >= 0


def test_score_file_exclusion_respects_word_boundaries():
    """'live' must not reject 'Olive Oil' or 'Delivery'."""
    assert score_file(
        {"filename": "Olive Oil - Delivery.mp3", "bitRate": 1000},
        {}, "Olive Oil - Delivery", exclude_words=["live"],
    ) >= 0


def test_rank_results_drops_excluded_candidate():
    """Given both versions from one user, only the clean one survives."""
    responses = [{
        "username": "u1",
        "hasFreeUploadSlot": True,
        "uploadSpeed": 1_000_000,
        "queueLength": 0,
        "files": [
            {"filename": "Talking Heads - Once in a Lifetime.mp3",
             "size": 1_000_000, "bitRate": 1000},
            {"filename": "Talking Heads - Once in a Lifetime (Instrumental).mp3",
             "size": 1_000_000, "bitRate": 1000},
        ],
    }]
    candidates = rank_results(
        responses, "Talking Heads - Once in a Lifetime",
        artist_filter="Talking Heads", exclude_words=["instrumental"],
    )
    assert [c["filename"] for c in candidates] == [
        "Talking Heads - Once in a Lifetime.mp3"
    ]


def test_rank_results_without_exclusions_keeps_both():
    """Omitting exclude_words preserves the existing behavior exactly."""
    responses = [_make_response("u1", "Artist - Song (Instrumental).mp3")]
    assert len(rank_results(responses, "Artist - Song")) == 1


def test_score_file_exclusion_matches_plural_form():
    """Real paths say "2001 Instrumentals Only" -- the singular term must catch it.

    Found against live Soulseek data: 22 of 1353 surviving candidates for
    'Dr. Dre - Still D.R.E. no:instrumental' were instrumental albums that
    slipped through purely on the trailing 's'.
    """
    assert score_file(
        {"filename": "storage\\Dr. Dre Instrumentals Only 2001\\04 Still D.R.E..flac",
         "bitRate": 1000},
        {}, "Dr. Dre - Still D.R.E.", exclude_words=["instrumental"],
    ) == -1


def test_score_file_exclusion_plural_does_not_match_unrelated_prefix():
    """Only a trailing 's' is tolerated, not an arbitrary suffix."""
    assert score_file(
        {"filename": "Artist - Instrumentation Study.mp3", "bitRate": 1000},
        {}, "Artist - Instrumentation Study", exclude_words=["instrumental"],
    ) >= 0


def test_score_file_exclusion_plural_is_blunt_by_design():
    """The trailing-'s' allowance also catches "Lives" for no:live.

    Pinned deliberately: the plural tolerance is what makes no:instrumental
    catch "Instrumentals Only" folders, and that is worth the rare false
    positive on a title like "Our Lives". Do not "fix" this without also
    handling the folder case that test_score_file_exclusion_matches_plural_form
    covers.
    """
    assert score_file(
        {"filename": "The Calling - Our Lives.mp3", "bitRate": 1000},
        {}, "The Calling - Our Lives", exclude_words=["live"],
    ) == -1
