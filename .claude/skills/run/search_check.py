"""Search-only smoke check: exercises slskd search + ezsk ranking.

Never enqueues a transfer. Usage: search_check.py [query]
"""
from __future__ import annotations

import sys
import time

from ezsk.client import SlskdClient
from ezsk.config import Config
from ezsk.search import rank_results

DEFAULT_QUERY = "Radiohead Creep"
ATTEMPTS = 3


def main() -> int:
    query = " ".join(sys.argv[1:]) or DEFAULT_QUERY
    config = Config.from_file()
    config.apply_env()

    client = SlskdClient(config)
    client.check_connection()
    print(f"query: {query!r}")

    # A freshly started slskd has not joined the distributed network yet and
    # returns nothing for a few seconds, so retry before believing a zero.
    responses: list[dict] = []
    for attempt in range(1, ATTEMPTS + 1):
        _, responses = client.search(query, timeout_ms=20000)
        if responses:
            break
        if attempt < ATTEMPTS:
            print(f"  no responses (attempt {attempt}/{ATTEMPTS}), peers may still be connecting...")
            time.sleep(5)
    print(f"responses: {len(responses)} users")

    ranked = rank_results(
        responses,
        query,
        preferred_format=config.preferred_format,
        min_bitrate=config.min_bitrate,
        min_bit_depth=config.min_bit_depth,
    )
    print(f"ranked candidates: {len(ranked)}")

    for c in ranked[:5]:
        name = c["filename"].rsplit("\\", 1)[-1]
        print(
            f"  {c['score']:6.1f}  {c['extension']:>5}  "
            f"{c['bitrate'] or '-':>5}kbps  {name[:70]}"
        )

    if not ranked:
        print("NOTE: no candidates ranked -- peers may be offline for this query.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
