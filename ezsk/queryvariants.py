from __future__ import annotations

import re

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
