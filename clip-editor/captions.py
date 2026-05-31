"""Burned-in caption generator.

Produces an ASS (Advanced SubStation Alpha) subtitle file styled like the
TikTok/Reels reference: bold white sans-serif with a thick black outline,
positioned in the lower-middle, 3 words at a time. ASS is rendered into the
video via FFmpeg's `subtitles` filter (libass).

cal.com transcripts only give segment-level timestamps, not word-level. We
distribute the timing evenly across each segment's text by chunk count.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WORDS_PER_LINE = 3


@dataclass
class TimedSegment:
    """A transcript segment positioned on the final output timeline."""

    start: float  # seconds since the start of the final output
    end: float
    text: str


def _format_time(seconds: float) -> str:
    """ASS time format: H:MM:SS.cc (centiseconds)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _chunk_words(text: str, words_per_chunk: int = WORDS_PER_LINE) -> list[str]:
    """Split text into ~N-word chunks. Returns lowercase chunks matching the
    reference style. Collapses internal whitespace and trims punctuation noise.
    """
    cleaned = re.sub(r"\s+", " ", text).strip().lower()
    if not cleaned:
        return []
    words = cleaned.split(" ")
    return [
        " ".join(words[i : i + words_per_chunk])
        for i in range(0, len(words), words_per_chunk)
    ]


def _escape_ass_text(s: str) -> str:
    """Minimal ASS escaping — newlines + curly braces are the load-bearing ones."""
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _header(video_width: int, video_height: int) -> str:
    """Generate the ASS header scaled to the actual output canvas.

    Font size and margins are calibrated against 720p (the original target);
    we scale linearly with height for taller canvases (vertical 9:16, square,
    etc.) so the caption stays the same relative size on screen.
    """
    scale = video_height / 720
    font_size = max(28, round(56 * scale))
    margin_v = max(40, round(90 * scale))
    margin_h = max(20, round(40 * (video_width / 1280)))
    outline = max(2, round(4 * scale))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,DejaVu Sans,{font_size},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,"
        f"{margin_h},{margin_h},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def build_ass(
    timed_segments: list[TimedSegment],
    video_width: int = 1280,
    video_height: int = 720,
) -> str:
    """Return the full ASS file content for the given timeline + canvas."""
    events: list[str] = []
    for seg in timed_segments:
        if seg.end <= seg.start:
            continue
        chunks = _chunk_words(seg.text)
        if not chunks:
            continue
        total = seg.end - seg.start
        per_chunk = total / len(chunks)
        for i, chunk in enumerate(chunks):
            chunk_start = seg.start + i * per_chunk
            chunk_end = chunk_start + per_chunk
            events.append(
                f"Dialogue: 0,{_format_time(chunk_start)},{_format_time(chunk_end)},"
                f"Default,,0,0,0,,{_escape_ass_text(chunk)}"
            )
    return _header(video_width, video_height) + "\n".join(events) + "\n"
