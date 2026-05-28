"""MCP tool implementations.

Each tool function returns a JSON-serializable dict matching the spec output shape.
Errors raise; the MCP runtime converts them to tool errors visible to Claude.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import drive
import editor
import downloads
import transcript

logger = logging.getLogger(__name__)

MAX_INPUT_BYTES = 500 * 1024 * 1024  # 500 MB cap on source clips


# ---------- helpers ----------


def _load_segments(transcript_file_id: str) -> tuple[list[transcript.Segment], str]:
    content = drive.read_text_file(transcript_file_id)
    try:
        return transcript.parse_transcript(content), content
    except transcript.TranscriptParseError as exc:
        # Surface the raw content so Claude can diagnose format issues.
        preview = content[:500]
        raise ValueError(
            f"Failed to parse transcript {transcript_file_id}: {exc}. "
            f"First 500 chars: {preview!r}"
        ) from exc


def _segments_to_ranges(
    segments: list[transcript.Segment],
    keep_segments: Optional[list[int]],
    keep_ranges: Optional[list[dict]],
) -> tuple[list[editor.Range], list[transcript.Segment]]:
    """Turn either keep_segments or keep_ranges into editor.Range objects.

    Also returns the matching segment objects (for preview output) — empty when
    keep_ranges is used since ranges don't necessarily map to single segments.
    """
    if keep_segments is None and keep_ranges is None:
        raise ValueError("Provide either keep_segments or keep_ranges")
    if keep_segments is not None and keep_ranges is not None:
        raise ValueError("Provide keep_segments OR keep_ranges, not both")

    if keep_segments is not None:
        ranges: list[editor.Range] = []
        kept: list[transcript.Segment] = []
        for idx in keep_segments:
            if idx < 0 or idx >= len(segments):
                raise ValueError(f"Segment index {idx} out of range (0..{len(segments) - 1})")
            seg = segments[idx]
            if seg.end is None:
                # Last segment: estimate end as start + median duration of others.
                others = [s.duration for s in segments if s.duration is not None]
                est = (sum(others) / len(others)) if others else 5.0
                end = seg.start + est
            else:
                end = seg.end
            ranges.append(editor.Range(seg.start, end))
            kept.append(seg)
        return ranges, kept

    # keep_ranges path
    parsed_ranges: list[editor.Range] = []
    for r in keep_ranges or []:
        start = transcript.parse_timestamp(r["start"])
        end = transcript.parse_timestamp(r["end"])
        if end <= start:
            raise ValueError(f"Range end {r['end']} must be after start {r['start']}")
        parsed_ranges.append(editor.Range(start, end))
    return parsed_ranges, []


def _estimate_padded_duration(ranges: list[editor.Range], pad: bool) -> float:
    """Estimate cut duration without knowing file duration (preview-only)."""
    total = 0.0
    for r in ranges:
        start = max(0.0, r.start - (editor.PAD_PRE if pad else 0.0))
        end = r.end + (editor.PAD_POST if pad else 0.0)
        total += max(0.0, end - start)
    return total


def _parse_clip_name(filename: str) -> dict:
    """Classify a clip filename into type + label.

    Conventions:
        '{Student} - Highlight - {Label}.mp4' → highlight
        '{Student} - {Label}.mp4'             → dimension
    """
    stem = filename
    if stem.lower().endswith(".mp4"):
        stem = stem[:-4]
    parts = [p.strip() for p in stem.split(" - ")]
    if len(parts) >= 3 and parts[1].lower() == "highlight":
        return {"type": "highlight", "label": " - ".join(parts[2:])}
    if len(parts) >= 2:
        return {"type": "dimension", "label": " - ".join(parts[1:])}
    return {"type": "dimension", "label": stem}


# ---------- tools ----------


def parse_transcript_tool(file_id: str) -> dict:
    segments, _ = _load_segments(file_id)
    return {
        "segments": [s.to_dict() for s in segments],
        "total_duration_estimate": transcript.total_duration_estimate(segments),
        "segment_count": len(segments),
    }


def preview_edit_tool(
    file_id: str,
    keep_segments: Optional[list[int]] = None,
    keep_ranges: Optional[list[dict]] = None,
    pad: bool = True,
) -> dict:
    segments, _ = _load_segments(file_id)
    ranges, kept_segments = _segments_to_ranges(segments, keep_segments, keep_ranges)

    estimated = _estimate_padded_duration(ranges, pad)
    original = transcript.total_duration_estimate(segments) or 0.0

    preview = []
    if kept_segments:
        for seg in kept_segments:
            preview.append(
                {
                    "index": seg.index,
                    "start_ts": seg.start_ts,
                    "end_ts": seg.end_ts,
                    "speaker": seg.speaker,
                    "text": seg.text,
                }
            )
    else:
        # keep_ranges path — just echo the ranges back, no segment metadata.
        for r in ranges:
            preview.append(
                {
                    "start_ts": transcript.format_timestamp(r.start),
                    "end_ts": transcript.format_timestamp(r.end),
                }
            )

    return {
        "estimated_duration": round(estimated, 2),
        "original_duration_estimate": round(original, 2),
        "segments_kept": len(ranges),
        "segments_cut": max(0, len(segments) - len(ranges)) if keep_segments else None,
        "preview": preview,
    }


def edit_clip_tool(
    clip_file_id: str,
    transcript_file_id: str,
    keep_segments: Optional[list[int]] = None,
    keep_ranges: Optional[list[dict]] = None,
    output_name: str = "edited_clip.mp4",
    pad: bool = True,
) -> dict:
    # Sanitize output name — strip any path components.
    output_name = os.path.basename(output_name) or "edited_clip.mp4"
    if not output_name.lower().endswith(".mp4"):
        output_name = output_name + ".mp4"

    # Pre-flight size check via metadata before downloading.
    clip_meta = drive.get_metadata(clip_file_id)
    if clip_meta.size is not None and clip_meta.size > MAX_INPUT_BYTES:
        raise ValueError(
            f"Source clip {clip_meta.name} is {clip_meta.size / 1024 / 1024:.1f} MB, "
            f"over the {MAX_INPUT_BYTES // 1024 // 1024} MB limit. Trim the source first."
        )

    segments, _ = _load_segments(transcript_file_id)
    ranges, _ = _segments_to_ranges(segments, keep_segments, keep_ranges)

    token, work_dir = downloads.make_workspace()
    source_path = os.path.join(work_dir, "source.mp4")
    drive.download_file(clip_file_id, source_path)

    duration = editor.get_duration(source_path)
    padded = editor.apply_padding(ranges, duration, pad)

    output_path = os.path.join(work_dir, output_name)
    editor.cut_clip(source_path, padded, output_path, work_dir)

    # Remove the source + part files; keep only the final output.
    for entry in os.listdir(work_dir):
        full = os.path.join(work_dir, entry)
        if full != output_path and os.path.isfile(full):
            try:
                os.remove(full)
            except OSError:
                pass

    stored = downloads.register(token, output_path, output_name)
    final_duration = editor.get_duration(stored.path)

    ttl_hours = float(os.environ.get("DOWNLOAD_TTL_HOURS", downloads.DEFAULT_TTL_HOURS))
    return {
        "download_url": stored.download_url,
        "duration": round(final_duration, 2),
        "file_size_mb": round(stored.size_bytes / 1024 / 1024, 2),
        "segments_kept": len(ranges),
        "expires_in_hours": ttl_hours,
    }


def list_clips_tool(student_folder_id: str) -> dict:
    folder_meta = drive.get_metadata(student_folder_id)
    event_types: dict[str, dict] = {}

    for sub in drive.list_folder(student_folder_id):
        if sub.mime_type != drive.FOLDER_MIME:
            continue
        children = drive.list_folder(sub.id)

        mp4s = {f.name: f for f in children if f.name.lower().endswith(".mp4")}
        txts = {f.name: f for f in children if f.name.lower().endswith(".txt")}

        clips = []
        for mp4_name, mp4 in mp4s.items():
            stem = mp4_name[:-4]
            transcript_name = f"{stem} - Transcript.txt"
            txt = txts.get(transcript_name)
            parsed = _parse_clip_name(mp4_name)
            clips.append(
                {
                    "name": mp4_name,
                    "file_id": mp4.id,
                    "transcript_file_id": txt.id if txt else None,
                    "type": parsed["type"],
                    "label": parsed["label"],
                }
            )

        if clips:
            event_types[sub.name] = {"clips": clips}

    return {"student_folder_name": folder_meta.name, "event_types": event_types}
