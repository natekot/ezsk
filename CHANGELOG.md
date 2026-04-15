# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
