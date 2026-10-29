from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from ezsk.matching import is_various_artists, significant_words
from ezsk.normalize import normalize_query

LOSSLESS_EXTENSIONS = {".flac", ".wav", ".ape", ".alac", ".aiff", ".aif"}


@dataclass
class VerifyResult:
    passed: bool
    codec: str
    bitrate: int
    sample_rate: int
    bit_depth: int
    frequency_cutoff: float | None  # kHz, lossless only
    reason: str  # empty if passed


def _detect_frequency_cutoff(file_path: str) -> float | None:
    """Detect the highest frequency with significant energy.

    Returns cutoff in kHz, or None if analysis fails.
    """
    try:
        import numpy as np
        import soundfile as sf
        from scipy.fft import rfft, rfftfreq
    except ImportError:
        return None

    try:
        info = sf.info(file_path)
    except Exception:
        return None

    sr = info.samplerate
    duration = info.duration

    if duration < 5:
        return None  # too short for reliable analysis

    # Read 30 seconds from the middle
    mid = max(0, duration / 2 - 15)
    start_frame = int(mid * sr)
    frames_to_read = min(int(30 * sr), int(duration * sr) - start_frame)

    try:
        data, _ = sf.read(
            file_path, start=start_frame, frames=frames_to_read, dtype="float32",
        )
    except Exception:
        return None

    if data.ndim > 1:
        data = data.mean(axis=1)

    spectrum = np.abs(rfft(data))
    spectrum_db = 20 * np.log10(spectrum + 1e-10)
    freqs = rfftfreq(len(data), 1.0 / sr)

    # Noise floor: median of 20-21kHz band (above audible content)
    nyquist = sr / 2
    if nyquist > 21000:
        noise_mask = (freqs >= 20000) & (freqs <= 21000)
        noise_floor = float(np.median(spectrum_db[noise_mask]))
    else:
        noise_floor = float(np.percentile(spectrum_db, 5))

    # Walk backwards from Nyquist to find where energy exceeds noise floor
    threshold = noise_floor + 10
    cutoff_freq = 0.0
    for i in range(len(freqs) - 1, 0, -1):
        if spectrum_db[i] > threshold:
            cutoff_freq = float(freqs[i])
            break

    return round(cutoff_freq / 1000, 1)


def _check_integrity(file_path: str) -> str | None:
    """Decode the entire file to check for corruption.

    Returns an error string if corrupt, None if OK.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", file_path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        return None  # ffmpeg not installed, skip check
    except subprocess.TimeoutExpired:
        return "integrity check timed out"

    if result.returncode != 0 or result.stderr.strip():
        errors = result.stderr.strip().split("\n")
        return f"corrupt audio data ({len(errors)} decode errors)"
    return None


def verify_file(
    file_path: str,
    min_bitrate: int = 320,
    min_bit_depth: int = 16,
    min_cutoff_khz: float = 19.0,
) -> VerifyResult:
    """Verify audio file quality via metadata, integrity, and spectral analysis."""
    try:
        import mutagen
    except ImportError:
        raise ImportError(
            "mutagen is required for --verify. "
            "Install with: pip install ezsk[verify]"
        )

    ext = os.path.splitext(file_path)[1].lower()
    is_lossless = ext in LOSSLESS_EXTENSIONS

    try:
        f = mutagen.File(file_path)
    except Exception:
        f = None
    if f is None:
        return VerifyResult(
            passed=False, codec="unknown", bitrate=0, sample_rate=0,
            bit_depth=0, frequency_cutoff=None,
            reason="could not read file metadata",
        )

    info = f.info
    codec = type(f).__name__.lower()
    sample_rate = getattr(info, "sample_rate", 0)
    bitrate = round(getattr(info, "bitrate", 0) / 1000) if getattr(info, "bitrate", 0) else 0
    bit_depth = getattr(info, "bits_per_sample", 0) or 0

    # Metadata checks
    if not is_lossless and bitrate and bitrate < min_bitrate:
        return VerifyResult(
            passed=False, codec=codec, bitrate=bitrate, sample_rate=sample_rate,
            bit_depth=bit_depth, frequency_cutoff=None,
            reason=f"bitrate {bitrate}kbps below minimum {min_bitrate}kbps",
        )

    if is_lossless and bit_depth and bit_depth < min_bit_depth:
        return VerifyResult(
            passed=False, codec=codec, bitrate=bitrate, sample_rate=sample_rate,
            bit_depth=bit_depth, frequency_cutoff=None,
            reason=f"bit depth {bit_depth} below minimum {min_bit_depth}",
        )

    # Integrity check: decode entire file to catch truncation/corruption
    integrity_error = _check_integrity(file_path)
    if integrity_error:
        return VerifyResult(
            passed=False, codec=codec, bitrate=bitrate, sample_rate=sample_rate,
            bit_depth=bit_depth, frequency_cutoff=None,
            reason=integrity_error,
        )

    # Spectral analysis for lossless files
    frequency_cutoff = None
    if is_lossless:
        frequency_cutoff = _detect_frequency_cutoff(file_path)
        if frequency_cutoff is not None and frequency_cutoff < min_cutoff_khz:
            return VerifyResult(
                passed=False, codec=codec, bitrate=bitrate,
                sample_rate=sample_rate, bit_depth=bit_depth,
                frequency_cutoff=frequency_cutoff,
                reason=f"frequency cutoff {frequency_cutoff}kHz suggests transcode "
                       f"(minimum {min_cutoff_khz}kHz)",
            )

    return VerifyResult(
        passed=True, codec=codec, bitrate=bitrate, sample_rate=sample_rate,
        bit_depth=bit_depth, frequency_cutoff=frequency_cutoff, reason="",
    )


def _primary_artist(artist: str) -> str:
    """Extract the primary artist, stripping featured/secondary artists."""
    # Remove (feat. X), (ft. X) parentheticals
    artist = re.sub(r"\s*\((?:feat|ft)\.?\s+[^)]*\)", "", artist, flags=re.IGNORECASE)
    # Remove bare "feat. X" / "ft. X" / "featuring X" at end
    artist = re.sub(r"\s*\b(?:feat|ft|featuring)\.?\s*.*$", "", artist, flags=re.IGNORECASE)
    # Take first artist from semicolon-separated list
    artist = artist.split(";")[0].strip()
    return artist


_MIN_MATCH = 0.6


def read_artist_title(file_path: str) -> tuple[str, str] | None:
    """The file's (artist, title) tags, or None unless it has both."""
    try:
        import mutagen
    except ImportError:
        return None

    try:
        f = mutagen.File(file_path, easy=True)
    except Exception:
        return None
    if f is None:
        return None

    artist = ((f.get("artist") or [""])[0] or "").strip()
    title = ((f.get("title") or [""])[0] or "").strip()
    if not artist or not title:
        return None
    return artist, title


def check_metadata_match(file_path: str, query: str) -> str | None:
    """Check if the file's embedded tags match the query.

    Returns an error string if the tags indicate a wrong track, None if OK.
    """
    tags = read_artist_title(file_path)
    # Skip check if tags are missing
    if tags is None:
        return None
    tag_artist, tag_title = tags

    if " - " not in query:
        return None

    q_artist, q_title = query.split(" - ", 1)

    nq_artist = significant_words(_primary_artist(q_artist).replace("_", " "))
    nq_title = significant_words(q_title.replace("_", " "), drop_fillers=True)
    nt_artist = significant_words(tag_artist.replace("_", " "))
    nt_title = significant_words(tag_title.replace("_", " "), drop_fillers=True)
    # Cross-match tag words across artist+title to tolerate swapped tags
    # and remixer credits appearing in either field.
    t_all = nt_artist | nt_title

    artist_is_va = is_various_artists(q_artist)
    title_match = len(nq_title & t_all) / len(nq_title) if nq_title else 1.0

    if artist_is_va:
        # Compilation: don't trust the artist tag; require title to carry it
        if title_match < _MIN_MATCH:
            return (
                f"metadata mismatch: tags say \"{tag_artist} - {tag_title}\", "
                f"expected \"{q_artist} - {q_title}\""
            )
        return None

    artist_match = len(nq_artist & t_all) / len(nq_artist) if nq_artist else 1.0

    # Reject if EITHER side is weak: a real match has strong artist AND title.
    if artist_match < _MIN_MATCH or title_match < _MIN_MATCH:
        return (
            f"metadata mismatch: tags say \"{tag_artist} - {tag_title}\", "
            f"expected \"{q_artist} - {q_title}\""
        )

    return None
