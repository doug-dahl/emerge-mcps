"""Build the music track that underscores the narrative.

The score has three phases:
    1. Rising action — `rising action.mp3` plays under the struggle segments
    2. Turning point — pure silence for N seconds
    3. Triumph     — `triumph.mp3` plays under the triumph segments

Tracks are looped and trimmed to fit. The final track is the same length as
the video so we can mix it 1:1 without offset bookkeeping.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

DEFAULT_RISING_ACTION = "assets/rising action.mp3"
DEFAULT_TRIUMPH = "assets/triumph.mp3"


class ScoreError(RuntimeError):
    pass


@dataclass
class ScoreLayout:
    rising_duration: float  # seconds of rising-action music
    pause_duration: float  # seconds of silence
    triumph_duration: float  # seconds of triumph music


def _rising_path() -> str:
    return os.environ.get("RISING_ACTION_PATH", DEFAULT_RISING_ACTION)


def _triumph_path() -> str:
    return os.environ.get("TRIUMPH_PATH", DEFAULT_TRIUMPH)


def _ensure_asset_exists(path: str, label: str) -> None:
    if not os.path.isfile(path):
        raise ScoreError(
            f"{label} asset not found at {path!r}. "
            f"Drop the MP3 into clip-editor/assets/ and redeploy, "
            f"or set {'RISING_ACTION_PATH' if label == 'rising_action' else 'TRIUMPH_PATH'}."
        )


def build_score_track(layout: ScoreLayout, output_path: str, work_dir: str) -> None:
    """Render a single MP3-like AAC track that follows the layout.

    Uses FFmpeg in one call with `aevalsrc` for the silence portion and
    `-stream_loop -1` to loop the source MP3s until trimmed to length.
    """
    rising = _rising_path()
    triumph = _triumph_path()
    _ensure_asset_exists(rising, "rising_action")
    _ensure_asset_exists(triumph, "triumph")

    rising_part = os.path.join(work_dir, "score_rising.m4a")
    silence_part = os.path.join(work_dir, "score_silence.m4a")
    triumph_part = os.path.join(work_dir, "score_triumph.m4a")

    # 1. Rising action — looped + trimmed to rising_duration.
    _render_audio_segment(
        cmd=[
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            rising,
            "-t",
            f"{layout.rising_duration:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            rising_part,
        ],
        label="rising-action loop",
    )

    # 2. Silence — generated via anullsrc.
    _render_audio_segment(
        cmd=[
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{layout.pause_duration:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            silence_part,
        ],
        label="turning-point silence",
    )

    # 3. Triumph — looped + trimmed.
    _render_audio_segment(
        cmd=[
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            triumph,
            "-t",
            f"{layout.triumph_duration:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            triumph_part,
        ],
        label="triumph loop",
    )

    # Concat into one continuous score track.
    concat_list = os.path.join(work_dir, "score_concat.txt")
    with open(concat_list, "w") as fh:
        for p in (rising_part, silence_part, triumph_part):
            fh.write(f"file '{p}'\n")
    _render_audio_segment(
        cmd=[
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
            output_path,
        ],
        label="score concat",
    )


def _render_audio_segment(cmd: list[str], label: str) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise ScoreError(
            f"FFmpeg failed during {label}: "
            f"{exc.stderr.decode(errors='replace')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ScoreError(f"FFmpeg timed out during {label}") from exc
