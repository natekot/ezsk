from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ezsk.ai_parser import parse_file_with_ai
from ezsk.display import print_error, print_status

_SKIP_EXTENSIONS = {".log", ".json", ".txt.bak", ".tracks.txt"}
_SKIP_PREFIXES = (".", "queued", "dead")


def _is_watchable(name: str) -> bool:
    if any(name.startswith(p) for p in _SKIP_PREFIXES):
        return False
    if any(name.endswith(e) for e in _SKIP_EXTENSIONS):
        return False
    return True


@dataclass
class FileInfo:
    name: str
    size: int
    modified: str
    status: str  # "cached" | "unparsed" | "parsing" | "error"
    track_count: int | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "modified": self.modified,
            "status": self.status,
            "track_count": self.track_count,
            "error": self.error,
        }


class WatchDirService:
    def __init__(self, watch_dir: str, anthropic_api_key: str = ""):
        self.watch_dir = os.path.expanduser(watch_dir)
        self.anthropic_api_key = anthropic_api_key
        self._parsing: set[str] = set()
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def _file_path(self, filename: str) -> str:
        return os.path.join(self.watch_dir, filename)

    def _cache_path(self, filename: str) -> str:
        return self._file_path(filename) + ".tracks.txt"

    def list_files(self) -> list[FileInfo]:
        if not os.path.isdir(self.watch_dir):
            return []
        result = []
        for name in sorted(os.listdir(self.watch_dir)):
            if not _is_watchable(name):
                continue
            fpath = self._file_path(name)
            if not os.path.isfile(fpath):
                continue
            stat = os.stat(fpath)
            modified = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime))

            with self._lock:
                if name in self._parsing:
                    status = "parsing"
                    track_count = None
                    error = None
                elif name in self._errors:
                    status = "error"
                    track_count = None
                    error = self._errors[name]
                else:
                    cache = self._cache_path(name)
                    if os.path.exists(cache) and os.path.getmtime(cache) >= stat.st_mtime:
                        status = "cached"
                        track_count = sum(1 for line in open(cache) if line.strip())
                        error = None
                    else:
                        status = "unparsed"
                        track_count = None
                        error = None

            result.append(FileInfo(
                name=name, size=stat.st_size, modified=modified,
                status=status, track_count=track_count, error=error,
            ))
        return result

    def parse_file(self, filename: str) -> list[str]:
        """AI-parse a file and write the cache. Blocking."""
        fpath = self._file_path(filename)
        cache = self._cache_path(filename)

        with self._lock:
            self._parsing.add(filename)
            self._errors.pop(filename, None)

        try:
            # Delete stale cache
            if os.path.exists(cache):
                os.unlink(cache)

            tracks = parse_file_with_ai(fpath, self.anthropic_api_key)
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(cache), suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    f.write("\n".join(tracks) + "\n")
                os.replace(tmp, cache)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

            with self._lock:
                self._parsing.discard(filename)
            return tracks
        except Exception as e:
            print_error(f"Parse failed for {filename}: {e}")
            with self._lock:
                self._parsing.discard(filename)
                self._errors[filename] = str(e)
            raise

    def read_cached(self, filename: str) -> list[str]:
        cache = self._cache_path(filename)
        if not os.path.exists(cache):
            return []
        with open(cache) as f:
            return [line.strip() for line in f if line.strip()]

    def remove_file(self, filename: str) -> None:
        fpath = self._file_path(filename)
        cache = self._cache_path(filename)
        if os.path.exists(fpath):
            os.unlink(fpath)
        if os.path.exists(cache):
            os.unlink(cache)
        with self._lock:
            self._parsing.discard(filename)
            self._errors.pop(filename, None)

    def save_uploaded(self, filename: str, data: bytes) -> None:
        Path(self.watch_dir).mkdir(parents=True, exist_ok=True)
        fpath = self._file_path(filename)
        # Delete stale cache on overwrite
        cache = self._cache_path(filename)
        if os.path.exists(cache):
            os.unlink(cache)
        with open(fpath, "wb") as f:
            f.write(data)

    def collect_all_queries(self) -> list[str]:
        """Parse any unparsed files, then return all cached queries."""
        queries = []
        for info in self.list_files():
            if info.status == "unparsed":
                try:
                    self.parse_file(info.name)
                except Exception as e:
                    print_error(f"Failed to parse {info.name}: {e}")
                    continue
            tracks = self.read_cached(info.name)
            queries.extend(tracks)
        return queries
