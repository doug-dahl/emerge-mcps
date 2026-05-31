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


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,40,40,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(timed_segments: list[TimedSegment]) -> str:
    """Return the full ASS file content for the given timeline."""
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
    return HEADER + "\n".join(events) + "\n"
