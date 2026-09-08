# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Inline exclusion terms in queries: `no:instrumental`, `no:live`,
  `no:"radio edit"`. Results whose remote path contains the term are dropped
  from the ranking. Matching is whole-word, case-insensitive, and covers the
  full path (so a `Karaoke Hits` folder is caught). Works from the CLI, track
  files, the web UI queue box and the Replace dialog; the term is stripped
  before the search is sent to slskd and before the output file is named.

### Removed
- Docker-based slskd management (`--docker`, `ezsk/docker.py`, the `docker`
  install extra, and the Docker option in `ezsk --setup`). Use `--managed` for
  an ezsk-managed slskd, or point `[slskd] url`/`api_key` at your own instance.

### Changed
- `soulseek_username` moved from the `[docker]` section to `[slskd]` in
  `config.toml`. Existing configs are still read from `[docker]` for
  compatibility.

## [0.1.0] - 2025-04-14

### Added
- Initial release
- Batch download from text files and CSV playlists
- AI-powered search query simplification and result matching (Claude)
- AI-powered playlist parsing from text, images, and PDFs
- Watch directory for automatic playlist ingestion
- Live web dashboard with real-time status updates
- Web-based setup wizard for first-run configuration
- Embedded slskd binary management (no Docker required)
- Cross-platform path handling via platformdirs
- Auto-update checker (GitHub releases)
- System tray icon (macOS via rumps, Windows via pystray)
- PyInstaller packaging for macOS (.app) and Windows (.exe)
- Docker-based slskd management (alternative to embedded)
- Duplicate detection via local file index
- Queue management with automatic retry and dead letter queue
- Audio verification (bitrate, bit depth, format validation)
