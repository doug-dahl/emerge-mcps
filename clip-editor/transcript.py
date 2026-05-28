"""Cal.com-style timestamped transcript parser.

Line format: `[MM:SS.S] (speaker N) text...` or `[HH:MM:SS.S] (speaker N) text...`
Each segment's end is inferred from the next segment's start.
The last segment has end=None / duration=None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional

LINE_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?\.?\d*)\]\s*\(([^)]+)\)\s*(.*)")


@dataclass
class Segment:
    index: int
    start: float
    start_ts: str
    end: Optional[float]
    end_ts: Optional[str]
    duration: Optional[float]
    speaker: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


class TranscriptParseError(ValueError):
    pass


def parse_timestamp(ts: str) -> float:
    """Convert 'MM:SS.S' or 'HH:MM:SS.S' to seconds."""
    parts = ts.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise TranscriptParseError(f"Unrecognized timestamp format: {ts!r}")


def format_timestamp(seconds: Optional[float]) -> Optional[str]:
    """Format seconds back into 'MM:SS.S' (or 'HH:MM:SS.S' when >= 1h)."""
    if seconds is None:
        return None
    if seconds >= 3600:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds - h * 3600 - m * 60
        return f"{h:02d}:{m:02d}:{s:04.1f}"
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


def parse_transcript(content: str) -> list[Segment]:
    """Parse a transcript file's contents into a list of Segment.

    Lines that don't match the timestamp format are appended to the current
    segment's text (handles multi-line transcripts).
    """
    raw: list[tuple[float, str, str]] = []  # (start_seconds, speaker, text)
    for line in content.splitlines():
        match = LINE_RE.match(line.strip())
        if match:
            ts, speaker, text = match.groups()
            raw.append((parse_timestamp(ts), speaker.strip(), text.strip()))
        elif raw and line.strip():
            start, speaker, prev_text = raw[-1]
            raw[-1] = (start, speaker, (prev_text + " " + line.strip()).strip())

    if not raw:
        raise TranscriptParseError("No timestamped segments found in transcript")

    segments: list[Segment] = []
    for i, (start, speaker, text) in enumerate(raw):
        if i + 1 < len(raw):
            end = raw[i + 1][0]
            duration = end - start
        else:
            end = None
            duration = None
        segments.append(
            Segment(
                index=i,
                start=start,
                start_ts=format_timestamp(start),
                end=end,
                end_ts=format_timestamp(end),
                duration=duration,
                speaker=speaker,
                text=text,
            )
        )
    return segments


def total_duration_estimate(segments: list[Segment]) -> Optional[float]:
    """Best-effort total — the start of the last segment if its end is unknown."""
    if not segments:
        return None
    last = segments[-1]
    return last.end if last.end is not None else last.start


def normalize_to_clip_start(segments: list[Segment]) -> tuple[list[Segment], float]:
    """Shift all timestamps so the first segment starts at 0.

    Cal.com keeps original-recording timestamps even when the clip is just a
    section of the full recording (e.g. first transcript line at 03:58.9 for
    a clip that itself starts at 00:00.0). Returns the shifted segments and
    the offset (in seconds) that was subtracted.
    """
    if not segments:
        return [], 0.0
    offset = segments[0].start
    if offset == 0:
        return segments, 0.0
    shifted: list[Segment] = []
    for s in segments:
        new_start = s.start - offset
        new_end = s.end - offset if s.end is not None else None
        shifted.append(
            Segment(
                index=s.index,
                start=new_start,
                start_ts=format_timestamp(new_start),
                end=new_end,
                end_ts=format_timestamp(new_end),
                duration=s.duration,
                speaker=s.speaker,
                text=s.text,
            )
        )
    return shifted, offset
