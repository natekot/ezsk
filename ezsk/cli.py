from __future__ import annotations

import argparse
import signal
import sys
from urllib.parse import urlparse

from ezsk.config import Config, init_config
from ezsk.display import print_error, print_status, print_summary
from ezsk.download import download_batch, download_song
from ezsk.filereader import read_queries_from_file
from ezsk.normalize import query_hash
from ezsk.state import load_state, save_state


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ezsk",
        description="Soulseek download automation via slskd",
    )
    p.add_argument("query", nargs="*", help="Song query (e.g. 'Artist - Title')")
    p.add_argument("-f", "--file", help="File with one query per line")
    p.add_argument(
        "-c", "--columns",
        help="Columns to use from delimited file (e.g. 'artist,title' or '{artist} - {title}')",
    )
    p.add_argument(
        "-n", "--limit", type=int,
        help="Limit number of queries to process",
    )
    p.add_argument("--init", action="store_true", help="Create default config file")
    p.add_argument("--setup", action="store_true", help="Interactive setup wizard")

    conn = p.add_argument_group("connection")
    conn.add_argument("--url", help="slskd URL (default: http://localhost:5030)")
    conn.add_argument("--api-key", help="slskd API key")

    out = p.add_argument_group("output")
    out.add_argument("-o", "--output-dir", help="Output directory")
    out.add_argument("-p", "--output-pattern", help="Output filename pattern")
    out.add_argument("-F", "--format", help="Preferred audio format (e.g. flac, mp3)")
    out.add_argument("--min-bitrate", type=int, help="Minimum bitrate for lossy files (default: 320)")
    out.add_argument("--min-bit-depth", type=int, help="Minimum bit depth for lossless files (default: 16)")
    out.add_argument("--verify", action="store_true",
        help="Verify downloaded file quality via spectral analysis")
    out.add_argument("--min-frequency-cutoff", type=float, default=None,
        help="Min frequency cutoff in kHz for transcode detection (default: 19.0)")

    adv = p.add_argument_group("advanced")
    adv.add_argument("--slskd-downloads-dir", help="Path to slskd downloads directory")
    adv.add_argument("--shared-dir", help="Directory to share with Soulseek users")
    adv.add_argument("-i", "--interactive", action="store_true", help="Interactive result selection")
    adv.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    adv.add_argument("--timeout", type=int, help="Download timeout in seconds")
    adv.add_argument("--state-file", help="Path to state file")
    adv.add_argument("--log-file", help="Path to log file")
    adv.add_argument("--managed", action="store_true",
        help="Manage slskd as a local process (download and start automatically)")
    adv.add_argument("--docker", action="store_true", help="Manage slskd via Docker (legacy)")
    adv.add_argument("--no-index", action="store_true",
        help="Disable output directory index (skip duplicate checking)")
    adv.add_argument("--reindex", action="store_true",
        help="Rebuild the output directory index from scratch")
    adv.add_argument("--status", action="store_true",
        help="Generate and open an HTML status dashboard")

    adv.add_argument("--serve", action="store_true",
        help="Start the web UI (requires ezsk[web])")
    adv.add_argument("--desktop", action="store_true",
        help="Launch as a desktop app (starts slskd + web UI, opens browser)")

    ai = p.add_argument_group("ai")
    ai.add_argument("--watch-dir", help="Directory of playlist files to parse and download")
    ai.add_argument("--anthropic-api-key", help="Anthropic API key for file parsing")

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.setup:
        from ezsk.setup import run_setup
        run_setup()
        return

    if args.init:
        init_config()
        return

    config = Config.from_file()
    config.apply_env()
    config.apply_args(args)

    if args.managed:
        from ezsk.slskd_manager import ensure_running
        ensure_running(config)

    if args.docker:
        from ezsk.docker import ensure_slskd_running
        ensure_slskd_running(config)

    # Load index (on by default)
    if not args.no_index:
        from ezsk.index import load_index
        config.index = load_index(config.output_dir)

    if config.log_file:
        from ezsk.display import set_log_file
        set_log_file(config.log_file)

    if args.desktop:
        try:
            from ezsk.desktop import main as desktop_main
        except ImportError:
            print_error("Desktop mode requires: pip install ezsk[web]")
            sys.exit(1)
        desktop_main()
        return

    if args.serve:
        try:
            from ezsk.web.app import run_server
        except ImportError:
            print_error("Web UI requires: pip install ezsk[web]")
            sys.exit(1)
        run_server(config)
        return

    if args.status:
        import webbrowser

        from ezsk.dashboard import generate_dashboard

        path = generate_dashboard(config)
        print_status(f"Dashboard: {path}")
        webbrowser.open(f"file://{path}")

    # Collect queries
    queries: list[str] = []

    if config.watch_dir:
        import os

        from ezsk.watch import WatchDirService

        if not config.anthropic_api_key:
            print_error("--watch-dir requires an Anthropic API key (--anthropic-api-key or ANTHROPIC_API_KEY)")
            sys.exit(1)

        watch_path = os.path.expanduser(config.watch_dir)
        if not os.path.isdir(watch_path):
            print_error(f"Watch directory not found: {watch_path}")
            sys.exit(1)

        watch_svc = WatchDirService(config.watch_dir, config.anthropic_api_key)
        files = watch_svc.list_files()

        if not files:
            print_status("No files found in watch directory.")
            return

        for info in files:
            if info.status == "cached":
                print_status(f"Cached: {info.name}")
                queries.extend(watch_svc.read_cached(info.name))
            else:
                print_status(f"Parsing: {info.name}")
                try:
                    parsed = watch_svc.parse_file(info.name)
                    print_status(f"  -> {len(parsed)} tracks extracted")
                    queries.extend(parsed)
                except Exception as e:
                    print_error(f"  Failed to parse {info.name}: {e}")
                    continue
    else:
        if args.columns and not args.file:
            print_error("--columns requires --file/-f")
            sys.exit(1)

        if args.file:
            queries.extend(read_queries_from_file(args.file, args.columns))

        if args.query:
            queries.extend(args.query)

    if not queries:
        from ezsk.config import DEFAULT_CONFIG_PATH
        if not DEFAULT_CONFIG_PATH.exists():
            print_status("No config found. Run 'ezsk --setup' for guided setup, or 'ezsk --init' for a blank config file.")
        else:
            parser.print_help()
        sys.exit(1)

    if args.limit:
        queries = queries[:args.limit]

    # Deduplicate by normalized hash and skip already-completed
    state = load_state(config.state_file)
    from ezsk.state import is_completed as _is_completed
    seen: set[str] = set()
    deduped: list[str] = []
    skipped_completed = 0
    for q in queries:
        h = query_hash(q)
        if h in seen:
            continue
        seen.add(h)
        if _is_completed(state, q):
            skipped_completed += 1
            continue
        deduped.append(q)
    removed = len(queries) - len(deduped) - skipped_completed
    if removed:
        print_status(f"Removed {removed} duplicate {'query' if removed == 1 else 'queries'} from input")
    if skipped_completed:
        print_status(f"Skipped {skipped_completed} already-completed {'query' if skipped_completed == 1 else 'queries'}")
    queries = deduped

    # Validate config
    errors = config.validate()
    if errors:
        for e in errors:
            print_error(e)
        print_status("Run 'ezsk --init' to create a config file, or pass required flags.")
        sys.exit(1)

    # Warn about HTTP to remote hosts
    parsed = urlparse(config.url)
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        print_error(
            "WARNING: Connecting to remote slskd over HTTP. "
            "API key will be sent in plain text. Consider using HTTPS."
        )

    # Check connection
    from ezsk.client import SlskdClient
    try:
        SlskdClient(config).check_connection()
    except Exception as e:
        print_error(f"Cannot connect to slskd at {config.url}: {e}")
        sys.exit(1)

    # Rebuild index if requested
    if args.reindex and not args.no_index:
        from ezsk.index import build_index, save_index
        config.index = build_index(config.output_dir)
        save_index(config.output_dir, config.index)
        print_status(f"Index rebuilt: {len(config.index)} tracks")

    # Handle Ctrl+C gracefully
    state = load_state(config.state_file)
    completed = failed = skipped = 0

    def handle_interrupt(sig, frame):
        save_state(config.state_file, state)
        try:
            from ezsk.client import SlskdClient
            SlskdClient(config).cleanup_searches()
        except Exception:
            pass
        print_summary(completed, failed, skipped)
        sys.exit(130)

    signal.signal(signal.SIGINT, handle_interrupt)

    if len(queries) == 1:
        success = download_song(queries[0], config)
        sys.exit(0 if success else 1)
    else:
        completed, failed, skipped = download_batch(queries, config, input_file=args.file)
        print_summary(completed, failed, skipped)
        sys.exit(0 if failed == 0 else 1)
