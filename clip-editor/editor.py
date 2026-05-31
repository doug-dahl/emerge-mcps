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

# Stitch target: 720p, 30fps, stereo 48kHz AAC. Forces all parts to identical
# codec params so -c copy concat works across disparate source recordings.
STITCH_WIDTH = 1280
STITCH_HEIGHT = 720
STITCH_FPS = 30
STITCH_AUDIO_RATE = 48000


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


def extract_encoded(input_path: str, start: float, end: float, output_path: str) -> None:
    """Extract a range and re-encode to the stitch target (1280x720, 30fps, AAC).

    Necessary for cross-source concat — `-c copy` only works when all parts
    share codec/resolution/fps. This normalizes everything so the final concat
    can still use `-c copy` and stay fast.

    Uses input-seek (`-ss` before `-i`) for fast seeking. The encode itself is
    libx264 at `-preset ultrafast` so a 30s 1080p clip encodes in a few seconds
    on Railway's shared CPU.
    """
    vf = (
        f"scale={STITCH_WIDTH}:{STITCH_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={STITCH_WIDTH}:{STITCH_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={STITCH_FPS}"
    )
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

    if len(ranges) == 1:
        _ffmpeg_extract(input_path, ranges[0].start, ranges[0].end, output_path)
        return

    part_paths: list[str] = []
    for i, r in enumerate(ranges):
        part = os.path.join(work_dir, f"part_{i:03d}.mp4")
        _ffmpeg_extract(input_path, r.start, r.end, part)
        part_paths.append(part)
    _ffmpeg_concat(part_paths, output_path, work_dir)


def concat_parts(part_paths: list[str], output_path: str, work_dir: str) -> None:
    """Public wrapper around the concat demuxer for already-prepared parts."""
    _ffmpeg_concat(part_paths, output_path, work_dir)


def generate_silent_black(duration: float, output_path: str) -> None:
    """Render a black-frame, silent-audio clip at the stitch target params.

    Used as the turning-point pause between rising-action and triumph phases.
    Same codec/resolution/fps as `extract_encoded` so concat with -c copy works.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={STITCH_WIDTH}x{STITCH_HEIGHT}:r={STITCH_FPS}",
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
