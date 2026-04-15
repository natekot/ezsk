from __future__ import annotations

import json
import os

MODEL = "claude-haiku-4-5-20251001"

MATCH_PROMPT = """\
You are a music track matcher. Given an original track query and a list of candidate filenames
from a file-sharing network, determine which filenames are the correct track.

Consider:
- The artist name (may appear differently: "Glenn Undergound" vs "Glenn Underground")
- The song title
- Remix/version info (e.g., "Lounge Excursion Mix" should match "Lounge Excursion")
- Ignore file paths, folder names, and file extensions when matching

Return ONLY a JSON array of 0-based indices of matching filenames.
If none match, return an empty array: []"""


def match_results(query: str, filenames: list[str], api_key: str) -> list[int]:
    import anthropic

    if not filenames:
        return []
    # Only send basenames to reduce tokens
    basenames = [os.path.basename(f.replace("\\", "/")) for f in filenames]
    numbered = "\n".join(f"[{i}] {name}" for i, name in enumerate(basenames))
    content = f"Track query: {query}\n\nCandidate filenames:\n{numbered}"

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=MATCH_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    if not message.content:
        return []
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
    try:
        indices = json.loads(raw)
        return [i for i in indices if isinstance(i, int) and 0 <= i < len(filenames)]
    except (json.JSONDecodeError, TypeError):
        return []
