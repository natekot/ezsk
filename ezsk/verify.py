from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

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


def check_metadata_match(file_path: str, query: str) -> str | None:
    """Check if the file's embedded tags match the query.

    Returns an error string if the tags indicate a wrong track, None if OK.
    """
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

    tag_artist = (f.get("artist", [""])[0] or "").strip()
    tag_title = (f.get("title", [""])[0] or "").strip()

    # Skip check if tags are missing
    if not tag_artist or not tag_title:
        return None

    if " - " not in query:
        return None

    q_artist, q_title = query.split(" - ", 1)

    # Normalize to primary artist only; replace underscores with spaces
    _STOP_WORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "is", "it", "my", "your", "x"}

    def _words(text: str) -> set[str]:
        return set(normalize_query(text.replace("_", " ")).split())

    def _significant_words(text: str) -> set[str]:
        return _words(text) - _STOP_WORDS

    nq_artist = _significant_words(_primary_artist(q_artist))
    nq_title = _words(q_title)
    nt_artist = _significant_words(tag_artist)
    nt_title = _words(tag_title)
    t_all = nt_artist | nt_title

    # Cross-match: allow artist/title swaps and remixer credits
    t_all = nt_artist | nt_title
    artist_match = len(nq_artist & t_all) / max(len(nq_artist), 1)
    title_match = len(nq_title & t_all) / max(len(nq_title), 1)

    # Reject if both artist and title are weak
    if artist_match < 0.5 and title_match < 0.5:
        return (
            f"metadata mismatch: tags say \"{tag_artist} - {tag_title}\", "
            f"expected \"{q_artist} - {q_title}\""
        )

    return None
