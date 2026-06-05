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
    """ASS time format: H:MM:SS.cc (centiseconds).

    Rounds to centiseconds first, then carries — so 59.999s becomes
    ``0:01:00.00`` rather than the malformed ``0:00:60.00`` that a naive
    ``%05.2f`` on the seconds field produces (libass mis-parses that and the
    caption lands at the wrong time).
    """
    cs_total = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


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

    Font size and outline are calibrated against the 720p target (56px / 4px
    outline) and scaled by the SHORTER screen dimension, so the caption keeps
    a consistent on-screen size whether the canvas is landscape (16:9, short
    side = height) or portrait/square (9:16, 4:5, 1:1, short side = width).

    Scaling by height alone was the original bug: a 9:16 canvas (1080x1920)
    produced a ~149px font on a 1080-wide frame, far too wide for a 3-word
    line. The vertical margin still scales with height so the caption sits in
    the lower third regardless of canvas, and is lifted a little above the
    very bottom (120px @ 720p base) so it clears the social-app UI chrome
    (TikTok/Reels/Shorts caption bars and action buttons) and stays visible.

    WrapStyle 0 (smart, balanced wrapping) is the safety net: any 3-word line
    that is still too wide for the frame wraps onto a second line instead of
    running off both edges and getting clipped.
    """
    short_side = min(video_width, video_height)
    scale = short_side / 720
    font_size = max(28, round(56 * scale))
    outline = max(2, round(4 * scale))
    margin_v = max(56, round(120 * (video_height / 720)))
    margin_h = max(20, round(40 * (video_width / 1280)))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "WrapStyle: 0\n"
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
