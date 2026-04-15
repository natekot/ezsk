from __future__ import annotations
import json

SYSTEM_PROMPT = """\
You are a music playlist parser. Extract every song/track from the provided file content.
Return ONLY a JSON array of strings. Each string should be in "Artist - Title" format.
If the artist is unknown, just use the title. Include every track you can identify.
Output nothing except the JSON array."""


def parse_file_with_ai(file_path: str, api_key: str, model: str = "claude-haiku-4-5-20251001") -> list[str]:
    """Read a file and use Claude to extract track queries from it."""
    import anthropic

    with open(file_path, encoding="utf-8", errors="replace") as f:
        content = f.read(200_000)  # cap at ~200KB to stay within context

    # Estimate output tokens from line count to avoid truncation
    line_count = content.count("\n") + 1
    estimated_output_tokens = line_count * 30
    max_tokens = min(max(8192, estimated_output_tokens), 65536)

    client = anthropic.Anthropic(api_key=api_key)

    # Use streaming for large requests to avoid SDK timeout on long generations
    raw_parts = []
    stop_reason = None
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        for text in stream.text_stream:
            raw_parts.append(text)
        stop_reason = stream.get_final_message().stop_reason

    if not raw_parts:
        raise ValueError(f"Empty response from API (stop_reason={stop_reason})")

    if stop_reason == "max_tokens":
        raise ValueError(
            f"Response truncated ({max_tokens} tokens). "
            f"File has ~{line_count} lines — try splitting it into smaller files."
        )

    raw = "".join(raw_parts).strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]  # remove opening ```json line
        raw = raw.rsplit("```", 1)[0]  # remove closing ```
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"Failed to parse AI response as JSON: {raw[:200]}")
