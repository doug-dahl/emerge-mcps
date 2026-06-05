"""FFmpeg cut + concat.

Stream-copy strategy (no re-encode):
    - `-ss` and `-to` BEFORE `-i` for fast input seeking (no full decode)
    - `-c copy` to avoid re-encoding — cuts land on the nearest keyframe at
      or before the requested time, so cuts are slightly longer than asked
      (≤ one GOP of slack, usually ~2s) but the render is near-instant.
    - For the highlight use case "rough cut is fine" — frame accuracy isn't
      worth the cost of decoding 1080p on Railway's shared CPU.

If we ever need frame-accurate cuts, add a `precise=True` flag that swaps in
libx264 re-encode with `-ss` AFTER `-i`.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

PAD_PRE = 0.15
PAD_POST = 0.25

# Silence-trim defaults — chosen to mimic a video editor's "trim silence"
# (Premiere/Descript/CapCut): anything quieter than the threshold for at least
# the minimum duration is a removable gap, and a short cushion of that silence
# is left around every kept span so speech doesn't start/end abruptly.
SILENCE_THRESHOLD_DB = -30.0   # absolute fallback when adaptive mode is off
SILENCE_MIN_DURATION = 0.5     # only gaps at least this long get trimmed
SILENCE_KEEP_PAD = 0.10        # silence left on each side of kept speech
# Adaptive threshold offset: by default the silence line is set this many dB
# below the clip's own peak (≈ auto-editor's "4% of peak"), so the cut adapts
# to each recording's level instead of a fixed dB. Larger = stricter (cuts only
# near-total silence); smaller = trims quieter audio too.
SILENCE_PEAK_OFFSET_DB = 28.0

# Stitch target: 30fps, stereo 48kHz AAC. Width/height are now per-render
# (driven by `aspect`), but everything inside one render uses the same dims
# so the final concat can still use `-c copy`.
STITCH_FPS = 30
STITCH_AUDIO_RATE = 48000

# Aspect presets — output canvas in pixels. The shorter side defaults to 1080
# (a sane phone/social target) with the other dim derived from the ratio.
ASPECT_PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1280, 720),
}

# Friendly aliases callers can use instead of the canonical strings above.
ASPECT_ALIASES: dict[str, str] = {
    "9:16": "9:16", "9x16": "9:16",
    "vertical": "9:16", "portrait": "9:16",
    "tiktok": "9:16", "reels": "9:16", "stories": "9:16",
    "1:1": "1:1", "1x1": "1:1", "square": "1:1",
    "4:5": "4:5", "4x5": "4:5", "instagram": "4:5",
    "16:9": "16:9", "16x9": "16:9",
    "horizontal": "16:9", "landscape": "16:9", "widescreen": "16:9",
}


def resolve_aspect(aspect: str) -> tuple[str, int, int]:
    """Return (canonical, width, height) for an aspect input string."""
    key = aspect.strip().lower()
    canonical = ASPECT_ALIASES.get(key)
    if canonical is None:
        raise FFmpegError(
            f"Unknown aspect {aspect!r}. Try one of: "
            f"{', '.join(sorted(set(ASPECT_PRESETS)))}"
        )
    w, h = ASPECT_PRESETS[canonical]
    return canonical, w, h


def build_video_filter(out_w: int, out_h: int, frame_speaker: str) -> str:
    """Compute the -vf filter chain for the encode.

    frame_speaker == "none": letterbox/pillarbox to fit the canvas (preserves
        the whole source frame, may produce black bars).
    frame_speaker == "right" / "left": scale-to-cover then crop+pan to the
        chosen half. Used for cal.com side-by-side recordings where the
        student sits in one panel — pans onto them so they fill the frame.
    """
    if frame_speaker not in ("none", "right", "left"):
        raise FFmpegError(
            f"frame_speaker must be 'none', 'right', or 'left' (got {frame_speaker!r})"
        )

    if frame_speaker == "none":
        return (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,fps={STITCH_FPS}"
        )

    # Scale to cover the canvas (no black bars), then crop a canvas-sized
    # window centered on the right or left panel of the source. After the
    # cover-scale, the source is at least out_w×out_h in both dims, so
    # iw/4 (left panel center) or 3*iw/4 (right panel center) is meaningful.
    if frame_speaker == "right":
        x_expr = rf"max(0\,min(iw-{out_w}\,3*iw/4-{out_w}/2))"
    else:  # left
        x_expr = rf"max(0\,min(iw-{out_w}\,iw/4-{out_w}/2))"

    return (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h}:{x_expr}:0,"
        f"setsar=1,fps={STITCH_FPS}"
    )


class FFmpegError(RuntimeError):
    pass


@dataclass
class Range:
    start: float
    end: float


def get_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffprobe failed: {exc.stderr.decode(errors='replace')}") from exc
    return float(out.decode().strip())


def apply_padding(ranges: list[Range], duration: float, pad: bool) -> list[Range]:
    if not pad:
        return [Range(max(0.0, r.start), min(duration, r.end)) for r in ranges]
    return [
        Range(max(0.0, r.start - PAD_PRE), min(duration, r.end + PAD_POST))
        for r in ranges
    ]


def merge_ranges(ranges: list[Range]) -> list[Range]:
    """Coalesce ranges that overlap or abut into single continuous ranges.

    Kept segments are cut individually and re-concatenated, but a transcript
    segment's end is the *next* segment's start, so an unbroken run of speech
    produces back-to-back ranges. Once padding (±PAD_PRE/PAD_POST) is applied
    those ranges overlap, and cutting each separately then concatenating
    replays the overlap — a visible backward jump + stuttered audio at every
    boundary ("record scratch"). Merging a contiguous run into one cut keeps
    the lead-in/tail-out padding only at the true start/end of the run.

    Only merges a *forward* overlap (ascending start that lands inside the
    current range), so a deliberately reordered or non-adjacent selection
    (e.g. keep [6, 4]) stays as distinct cuts.
    """
    if not ranges:
        return []
    merged = [Range(ranges[0].start, ranges[0].end)]
    for r in ranges[1:]:
        last = merged[-1]
        if last.start <= r.start <= last.end:
            last.end = max(last.end, r.end)
        else:
            merged.append(Range(r.start, r.end))
    return merged


def detect_silences(
    input_path: str,
    threshold_db: float = SILENCE_THRESHOLD_DB,
    min_silence: float = SILENCE_MIN_DURATION,
) -> list[Range]:
    """Find the silent spans in a clip's audio via ffmpeg's `silencedetect`.

    This is the audio-level half of a video editor's "trim silence": anything
    quieter than `threshold_db` for at least `min_silence` seconds is reported
    as a removable gap — no transcript involved. silencedetect writes
    `silence_start:` / `silence_end:` lines to stderr; we parse them into
    Ranges. A silence that runs to the end of the file emits a start with no
    matching end, so we close it at the clip duration.
    """
    cmd = [
        "ffmpeg",
        "-i",
        input_path,
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg silencedetect failed: {exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg silencedetect timed out after 180s") from exc

    stderr = proc.stderr.decode(errors="replace")
    silences: list[Range] = []
    start: float | None = None
    for line in stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].strip().split()[0])
        elif "silence_end:" in line:
            end = float(line.split("silence_end:")[1].split("|")[0].strip())
            if start is not None:
                silences.append(Range(start, end))
                start = None
    if start is not None:
        silences.append(Range(start, get_duration(input_path)))
    return silences


def keep_ranges_from_silences(
    silences: list[Range],
    duration: float,
    keep_pad: float = SILENCE_KEEP_PAD,
    min_clip: float = 0.0,
) -> list[Range]:
    """Invert silence spans into the audible spans to keep.

    `keep_pad` re-adds a short cushion of silence on each side of every kept
    span so speech doesn't start/end abruptly (what editors call "padding").
    After padding, abutting spans are coalesced into one continuous take.
    Kept spans shorter than `min_clip` are dropped (filters out a stray word or
    click between two long pauses).
    """
    keep: list[Range] = []
    cursor = 0.0
    for s in sorted(silences, key=lambda r: r.start):
        if s.start > cursor:
            keep.append(Range(cursor, s.start))
        cursor = max(cursor, s.end)
    if cursor < duration:
        keep.append(Range(cursor, duration))

    padded = [
        Range(max(0.0, r.start - keep_pad), min(duration, r.end + keep_pad))
        for r in keep
    ]
    merged = merge_ranges(padded)
    if min_clip > 0:
        merged = [r for r in merged if (r.end - r.start) >= min_clip]
    return merged


def measure_peak_db(input_path: str) -> float:
    """Return the clip's peak audio level in dBFS via ffmpeg's `volumedetect`.

    Used to set the silence threshold *relative to the recording's own loudness*
    rather than a fixed dB — the approach auto-edit tools use so the cut line
    adapts to each clip's level (a clip recorded hot vs. quiet gets the same
    treatment). volumedetect prints `max_volume: -3.0 dB` to stderr.
    """
    cmd = ["ffmpeg", "-i", input_path, "-af", "volumedetect", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg volumedetect failed: {exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg volumedetect timed out after 180s") from exc

    stderr = proc.stderr.decode(errors="replace")
    for line in stderr.splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].replace("dB", "").strip())
    raise FFmpegError(
        "volumedetect reported no max_volume — the clip may have no audio track."
    )


def adaptive_silence_threshold(
    input_path: str, peak_offset_db: float = SILENCE_PEAK_OFFSET_DB
) -> float:
    """Silence threshold set `peak_offset_db` below the clip's measured peak.

    Adapts the cut line to the recording's own level instead of assuming a
    fixed dB. Note this adapts to recording *level*, not signal-to-noise ratio:
    a room with a very high noise floor can still leave dead air above the
    threshold (that's the case VAD handles better).
    """
    return measure_peak_db(input_path) - peak_offset_db


def _ffmpeg_extract(input_path: str, start: float, end: float, output_path: str) -> None:
    """Extract a single range via input-seek stream copy."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        input_path,
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg extract failed (start={start}, end={end}): "
            f"{exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(
            f"ffmpeg extract timed out after 60s (start={start}, end={end})"
        ) from exc


def _ffmpeg_concat(part_paths: list[str], output_path: str, work_dir: str) -> None:
    concat_list = os.path.join(work_dir, "concat.txt")
    with open(concat_list, "w") as fh:
        for p in part_paths:
            fh.write(f"file '{p}'\n")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg concat failed: {exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg concat timed out after 60s") from exc


def extract_encoded(
    input_path: str,
    start: float,
    end: float,
    output_path: str,
    vf: str,
) -> None:
    """Extract a range and re-encode to the stitch target (size driven by `vf`).

    Necessary for cross-source concat — `-c copy` only works when all parts
    share codec/resolution/fps. This normalizes everything so the final concat
    can still use `-c copy` and stay fast.

    Uses input-seek (`-ss` before `-i`) for fast seeking. The encode itself is
    libx264 at `-preset ultrafast` so a 30s 1080p clip encodes in a few seconds
    on Railway's shared CPU.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        input_path,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(STITCH_AUDIO_RATE),
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg encode failed (start={start}, end={end}): "
            f"{exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(
            f"ffmpeg encode timed out after 120s (start={start}, end={end})"
        ) from exc


def cut_clip(input_path: str, ranges: list[Range], output_path: str, work_dir: str) -> None:
    """Cut input_path into the given (already-padded) ranges and write output_path."""
    if not ranges:
        raise FFmpegError("No ranges to cut")

    # Coalesce contiguous/overlapping ranges so an unbroken run of kept
    # segments is one smooth cut, not separate cuts that replay the padding.
    ranges = merge_ranges(ranges)

    if len(ranges) == 1:
        _ffmpeg_extract(input_path, ranges[0].start, ranges[0].end, output_path)
        return

    part_paths: list[str] = []
    for i, r in enumerate(ranges):
        part = os.path.join(work_dir, f"part_{i:03d}.mp4")
        _ffmpeg_extract(input_path, r.start, r.end, part)
        part_paths.append(part)
    _ffmpeg_concat(part_paths, output_path, work_dir)


def trim_silence_render(input_path: str, keep: list[Range], output_path: str) -> None:
    """Re-render a clip keeping only `keep` spans, dropping everything between.

    One re-encode pass using the `select`/`aselect` filters with `setpts`/
    `asetpts` to restamp timestamps — the standard "jump cut" recipe that video
    editors use to render an auto-trim. Frame-accurate (unlike the stream-copy
    `cut_clip`), which matters here because silence trimming produces many small
    cuts at non-keyframe boundaries that keyframe-snapping would smear.

    The kept-span list is wrapped in single quotes so the commas inside each
    `between(t,a,b)` aren't read as filtergraph filter separators.
    """
    if not keep:
        raise FFmpegError(
            "Nothing to keep — the whole clip is below the silence threshold. "
            "Loosen threshold_db or min_silence."
        )
    expr = "+".join(f"between(t,{r.start:.3f},{r.end:.3f})" for r in keep)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        f"select='{expr}',setpts=N/FRAME_RATE/TB",
        "-af",
        f"aselect='{expr}',asetpts=N/SR/TB",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg silence trim failed: {exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg silence trim timed out after 300s") from exc


def concat_parts(part_paths: list[str], output_path: str, work_dir: str) -> None:
    """Public wrapper around the concat demuxer for already-prepared parts."""
    _ffmpeg_concat(part_paths, output_path, work_dir)


def generate_silent_black(duration: float, output_path: str, out_w: int, out_h: int) -> None:
    """Render a black-frame, silent-audio clip at the given dimensions.

    Used as the turning-point pause between rising-action and triumph phases.
    Same codec/resolution/fps as `extract_encoded` so concat with -c copy works.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={out_w}x{out_h}:r={STITCH_FPS}",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate={STITCH_AUDIO_RATE}",
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg silent-black render failed: "
            f"{exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg silent-black render timed out") from exc


def mix_music_with_voice(
    video_path: str,
    music_path: str,
    output_path: str,
    voice_volume: float = 1.0,
    music_volume: float = 0.22,
) -> None:
    """Mix a music track under the existing voice track of a video.

    Voice stays prominent; music sits underneath at ~22% by default. Output
    duration matches the video — music shorter than video gets padded with
    silence; longer gets cut.
    """
    filter_complex = (
        f"[0:a]volume={voice_volume}[voice];"
        f"[1:a]volume={music_volume}[music];"
        f"[voice][music]amix=inputs=2:duration=first:dropout_transition=0[mixed]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        music_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[mixed]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg audio mix failed: {exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg audio mix timed out") from exc


def burn_captions(video_path: str, ass_path: str, output_path: str) -> None:
    """Burn an ASS subtitle file into the video. Re-encodes video; audio is copied."""
    # FFmpeg's subtitles filter needs forward slashes and escaped colons on the path.
    safe_path = ass_path.replace("\\", "/").replace(":", r"\:")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        f"subtitles={safe_path}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=240)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg caption burn failed: {exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg caption burn timed out") from exc
