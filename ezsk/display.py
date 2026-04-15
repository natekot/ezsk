from __future__ import annotations

import os
import sys
import time

_log_file = None


def set_log_file(path: str) -> None:
    global _log_file
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _log_file = open(path, "a")


def _log(msg: str) -> None:
    if _log_file:
        ts = time.strftime("%H:%M:%S")
        _log_file.write(f"{ts} {msg}\n")
        _log_file.flush()


def print_verbose(msg: str, verbose: bool) -> None:
    _log(f"[verbose] {msg}")
    if verbose:
        print(f"  [verbose] {msg}", file=sys.stderr)


def print_error(msg: str) -> None:
    _log(f"ERROR: {msg}")
    print(f"  ERROR: {msg}", file=sys.stderr)


def print_status(msg: str) -> None:
    _log(msg)
    print(f"  {msg}", file=sys.stderr)


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes / (1024 * 1024):.1f}MB"


def format_candidate(c: dict, index: int) -> str:
    ext = c["extension"].upper()
    bitrate = f"{c['bitrate']}kbps" if c["bitrate"] else ""
    bit_depth = f"{c['bit_depth']}bit" if c.get("bit_depth") else ""
    sample_rate = f"{c['sample_rate'] // 1000}kHz" if c.get("sample_rate") else ""
    size = format_size(c["size"])
    user = c["username"]
    score = c["score"]
    free = " [FREE]" if c["free_slot"] else ""
    basename = os.path.basename(c["filename"])

    quality_parts = [p for p in [ext, bitrate, bit_depth, sample_rate] if p]
    quality = " ".join(quality_parts)

    return f"  [{index + 1}] {basename}  ({quality}, {size}) from {user} (score: {score:.0f}){free}"


def interactive_select(candidates: list[dict], max_show: int = 20) -> dict | None:
    if not candidates:
        print_error("No candidates to select from.")
        return None

    show = candidates[:max_show]
    print(f"\n  Found {len(candidates)} results (showing top {len(show)}):\n")
    for i, c in enumerate(show):
        print(format_candidate(c, i))

    print()
    while True:
        try:
            choice = input("  Select [1-{}] (or 'q' to skip): ".format(len(show)))
        except (EOFError, KeyboardInterrupt):
            return None
        if choice.lower() == "q":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(show):
                return show[idx]
        except ValueError:
            pass
        print("  Invalid selection, try again.")


def print_summary(completed: int, failed: int, skipped: int) -> None:
    print(f"\n  Summary: {completed} downloaded, {failed} failed, {skipped} skipped")
