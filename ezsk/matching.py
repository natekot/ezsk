from __future__ import annotations

import re

from ezsk.normalize import normalize_query

STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
    "for", "is", "it", "my", "your", "x",
})

FILLER_WORDS: frozenset[str] = frozenset({
    "remix", "mix", "edit", "version", "radio", "original",
    "remaster", "remastered", "live", "demo", "mono", "stereo",
    "feat", "ft", "featuring",
})

VARIOUS_ARTISTS_TOKENS: frozenset[str] = frozenset({
    "various artists", "various", "va", "v a",
})


def word_matches(word: str, normalized_haystack: str) -> bool:
    """True if `word` appears as a whole token in `normalized_haystack`.

    `normalized_haystack` must already be passed through `normalize_query`,
    which strips punctuation to spaces and collapses whitespace.
    """
    if not word:
        return False
    return re.search(rf"\b{re.escape(word)}\b", normalized_haystack) is not None


def significant_words(text: str, *, drop_fillers: bool = False) -> set[str]:
    """Normalize text and return the set of non-stopword tokens."""
    words = set(normalize_query(text).split())
    words -= STOPWORDS
    if drop_fillers:
        words -= FILLER_WORDS
    return words


def is_various_artists(artist: str) -> bool:
    return normalize_query(artist) in VARIOUS_ARTISTS_TOKENS
