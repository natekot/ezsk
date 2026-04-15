# ezsk

Soulseek download automation via [slskd](https://github.com/slskd/slskd).

Search, download, and organize music from the Soulseek network using the slskd API. Supports batch processing from text files or CSV playlists, parallel downloads, AI-powered search, and a live web dashboard.

## Prerequisites

- Python 3.11+
- A running [slskd](https://github.com/slskd/slskd) instance (or let ezsk manage one)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
# With uv (recommended)
uv pip install .

# With pip
pip install .

# With all optional features
pip install ".[ai,web,verify]"
```

### Desktop app

Download a pre-built `.app` (macOS) or `.exe` (Windows) from the [Releases](https://github.com/ezsk-dev/ezsk/releases) page. No Python required.

## Quick start

```bash
# Interactive setup
ezsk --init

# Download a single track
ezsk "Artist - Title"

# Download from a text file (one query per line)
ezsk -f tracks.txt

# Download from a CSV file
ezsk -f playlist.csv -c "artist,title"

# Start the web UI
ezsk --serve
```

## Configuration

Config and data files are stored in OS-standard locations:

| OS | Config | Data |
|----|--------|------|
| macOS | `~/Library/Application Support/ezsk/` | `~/Library/Application Support/ezsk/` |
| Linux | `~/.config/ezsk/` | `~/.local/share/ezsk/` |
| Windows | `%APPDATA%\ezsk\` | `%LOCALAPPDATA%\ezsk\` |

### Config file

`config.toml`:

```toml
[slskd]
url = "http://localhost:5030"
api_key = ""
downloads_dir = ""    # path to slskd's downloads directory
shared_dir = ""       # directory to share with Soulseek users

[output]
dir = "./downloads"
pattern = "{artist} - {title}.{ext}"
format = "flac"       # preferred format: flac, mp3, etc.
min_bitrate = 320     # minimum bitrate for lossy files
min_bit_depth = 16    # minimum bit depth for lossless files

[docker]
soulseek_username = ""

[anthropic]
api_key = ""          # for AI-powered search and playlist parsing
watch_dir = ""        # directory of playlist files to parse
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `EZSK_SLSKD_URL` | slskd URL |
| `EZSK_SLSKD_API_KEY` | slskd API key |
| `EZSK_SLSKD_DOWNLOADS_DIR` | slskd downloads directory |
| `ANTHROPIC_API_KEY` | Anthropic API key for AI features |
| `EZSK_WATCH_DIR` | Watch directory for playlist parsing |

### CLI flags

```
usage: ezsk [-h] [-f FILE] [-c COLUMNS] [-n LIMIT] [--init]
            [--url URL] [--api-key API_KEY]
            [-o OUTPUT_DIR] [-p OUTPUT_PATTERN] [-F FORMAT]
            [--min-bitrate MIN_BITRATE] [--min-bit-depth MIN_BIT_DEPTH]
            [--slskd-downloads-dir DIR] [--shared-dir DIR]
            [-i] [-v] [--timeout TIMEOUT] [--max-results MAX_RESULTS]
            [--state-file FILE] [--log-file FILE] [--docker]
            [--index] [--reindex] [--status]
            [--watch-dir DIR] [--anthropic-api-key KEY]
            [query ...]
```

## Features

### Batch downloads

Process a list of tracks from a text file or CSV:

```bash
# Plain text (one "Artist - Title" per line)
ezsk -f tracks.txt

# CSV with column selection
ezsk -f playlist.csv -c "artist,title"

# Custom format template
ezsk -f playlist.csv -c "{artist} - {title}"

# Limit to first N tracks
ezsk -f tracks.txt -n 10
```

### Parallel downloads

Downloads run with 2 parallel workers by default, with rate-limited searches to avoid overwhelming slskd.

### AI-powered search

With an Anthropic API key, ezsk uses Claude to:
- Simplify complex queries for better search results
- Match search results to the intended track
- Parse playlist files (text, images, PDFs) into track listings

```bash
ezsk "Complex Artist;Featured Artist - Track Title - Some Remix" --anthropic-api-key sk-...
```

### Watch directory

Point ezsk at a directory of playlist files and it will use AI to extract track listings and download them:

```bash
ezsk --watch-dir ~/playlists --anthropic-api-key sk-...
```

### Web dashboard

Start the web UI with real-time status updates:

```bash
ezsk --serve
```

### Embedded slskd

ezsk can manage its own slskd instance — no separate install needed:

```bash
ezsk --managed
```

Or use Docker:

```bash
ezsk -f tracks.txt --docker
```

### Duplicate detection

Use `--index` to skip tracks already in your output directory:

```bash
ezsk -f tracks.txt --index
```

### Queue management

Failed downloads are automatically queued to `queued.txt` in the output directory for retry. Permanently failed tracks (after multiple attempts) go to `dead.txt`.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions and guidelines.

```bash
uv pip install -e ".[test,web,verify,ai]"
uv run pytest tests/ -v
```

## License

[MIT](LICENSE)
