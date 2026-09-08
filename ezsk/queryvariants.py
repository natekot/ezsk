from __future__ import annotations

import re

from ezsk.normalize import normalize_query

# Junk suffixes that add noise to Soulseek searches. These appear after
# the song title as " - Suffix" or in parentheses/brackets.
_JUNK_SUFFIXES = re.compile(
    r'(?:'
    r'\s*[-–]\s*(?:'
        r'(?:\d{4}\s+)?remaster(?:ed)?'  # "2013 Remaster", "Remastered"
        r'|album\s+ver\w*'                 # "Album Version", "Album Verison" (typo)
        r'|single\s+ver\w*'
        r'|original\s+mix'
        r'|radio\s+(?:mix|edit)'
        r'|bonus\s+track'
        r'|mono'
        r'|stereo'
        r'|live'
        r'|demo'
        r'|edit(?:ed)?'
    r')'
    r'|'
    r'\s*[\(\[]\s*(?:'
        r'(?:\d{4}\s+)?remaster(?:ed)?'
        r'|album\s+vers?i?on'
        r'|single\s+ver\w*'
        r'|original\s+mix'
        r'|radio\s+(?:mix|edit)'
        r'|bonus\s+track'
        r'|mono|stereo|live|demo'
    r')\s*[\)\]]'
    r').*$',
    re.IGNORECASE,
)


def clean_query(query: str) -> str:
    """Default cleaning applied to all queries."""
    parts = query.split(" - ", 1)
    if len(parts) == 2:
        artist, title = parts
        # Take first semicolon-separated artist
        if ";" in artist:
            artist = artist.split(";")[0].strip()
        # Strip (feat. ...) / (ft. ...)
        artist = re.sub(r'\s*\((?:feat|ft)\.?\s+[^)]*\)', '', artist, flags=re.IGNORECASE)
        title = re.sub(r'\s*\((?:feat|ft)\.?\s+[^)]*\)', '', title, flags=re.IGNORECASE)
        # Strip junk suffixes (remaster, album version, etc.)
        title = _JUNK_SUFFIXES.sub('', title).strip()
        if not title:
            title = parts[1].strip()  # fallback to original if over-stripped
        return f"{artist} - {title}"
    # No artist-title split — just strip feat
    return re.sub(r'\s*\((?:feat|ft)\.?\s+[^)]*\)', '', query, flags=re.IGNORECASE)


def strip_suffix(query: str) -> str | None:
    """Strip remix/version/edit suffixes. Returns None if unchanged."""
    parts = query.split(" - ", 1)
    if len(parts) != 2:
        return None
    artist, title = parts
    # Strip trailing " - Something Remix/Mix/Version/Edit/Remaster"
    stripped = re.sub(r'\s*[-–]\s+[^-]+(?:remix|mix|version|edit|remaster).*$', '', title, flags=re.IGNORECASE)
    # Strip trailing parenthetical tags
    stripped = re.sub(r'\s*\([^)]*\)\s*$', '', stripped).strip()
    if stripped and stripped != title:
        return f"{artist} - {stripped}"
    return None


# Inline exclusion tokens: `no:word` or `no:"some phrase"`. The `no:` must be
# preceded by whitespace or line start and followed immediately by a non-space
# character. A bare `-word` token was considered and rejected -- "artist -title"
# is a real shape in scraped and hand-typed lists, so a hyphen token would
# silently eat half of those queries. `no:` collides with nothing: not the
# " - " artist/title separator, not hyphenated names (Jay-Z), not trailing
# punctuation (Wham!), and not incidental colons (Sunday 1:15).
_EXCLUDE_TOKEN = re.compile(r'(?:^|\s)no:(?:"([^"]+)"|(\S+))', re.IGNORECASE)


def split_excludes(query: str) -> tuple[str, list[str]]:
    """Split inline `no:word` / `no:"some phrase"` tokens out of a query.

    Returns (query with the tokens removed, normalized exclusion terms). The
    terms are normalized here because `score_file` matches them against a
    `normalize_query`-normalized path -- an un-normalized "radio edit" or an
    accented term would never line up with the haystack.
    """
    terms: list[str] = []
    for phrase, word in _EXCLUDE_TOKEN.findall(query):
        term = normalize_query(phrase or word)
        if term:
            terms.append(term)
    if not terms:
        return query, []
    stripped = re.sub(r"\s+", " ", _EXCLUDE_TOKEN.sub("", query)).strip()
    return stripped, list(dict.fromkeys(terms))
