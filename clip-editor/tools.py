"""MCP tool implementations.

Each tool function returns a JSON-serializable dict matching the spec output shape.
Errors raise; the MCP runtime converts them to tool errors visible to Claude.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import captions
import drive
import editor
import downloads
import score
import transcript

logger = logging.getLogger(__name__)

MAX_INPUT_BYTES = 500 * 1024 * 1024  # 500 MB cap on source clips


# ---------- helpers ----------


def _load_segments(
    transcript_file_id: str,
) -> tuple[list[transcript.Segment], float, str]:
    """Load + parse a transcript and normalize timestamps to clip-relative.

    Returns (normalized segments, offset that was subtracted, raw content).
    """
    content = drive.read_text_file(transcript_file_id)
    try:
        raw_segments = transcript.parse_transcript(content)
    except transcript.TranscriptParseError as exc:
        preview = content[:500]
        raise ValueError(
            f"Failed to parse transcript {transcript_file_id}: {exc}. "
            f"First 500 chars: {preview!r}"
        ) from exc
    normalized, offset = transcript.normalize_to_clip_start(raw_segments)
    return normalized, offset, content


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
    segments, offset, _ = _load_segments(file_id)
    return {
        "segments": [s.to_dict() for s in segments],
        "total_duration_estimate": transcript.total_duration_estimate(segments),
        "segment_count": len(segments),
        "clip_offset_seconds": offset,
    }


def preview_edit_tool(
    file_id: str,
    keep_segments: Optional[list[int]] = None,
    keep_ranges: Optional[list[dict]] = None,
    pad: bool = True,
) -> dict:
    segments, _, _ = _load_segments(file_id)
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

    segments, _, _ = _load_segments(transcript_file_id)
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


@dataclass
class _ProcessedPart:
    """A part after extraction: the encoded sub-clips and the timed segments
    that those sub-clips correspond to (clip-relative, then later remapped to
    output-relative for caption timing).
    """

    encoded_paths: list[str]
    durations: list[float]
    timed_segments: list[captions.TimedSegment]  # output-relative timing, filled later


def _process_part(
    part: dict,
    work_dir: str,
    source_idx: int,
    metas: list[drive.DriveFile],
    vf: str,
) -> _ProcessedPart:
    """Download a source mp4, cut + re-encode each kept range, collect timing info."""
    label = part.get("label") or metas[source_idx].name
    try:
        clip_id = part["clip_file_id"]
        transcript_id = part.get("transcript_file_id")
        if not transcript_id:
            raise ValueError("missing transcript_file_id")

        segments, _, _ = _load_segments(transcript_id)
        keep_segments_arg = part.get("keep_segments")
        keep_ranges_arg = part.get("keep_ranges")
        ranges, kept_segments = _segments_to_ranges(
            segments, keep_segments_arg, keep_ranges_arg
        )

        source_path = os.path.join(work_dir, f"source_{source_idx:03d}.mp4")
        drive.download_file(clip_id, source_path)
        duration = editor.get_duration(source_path)
        padded = editor.apply_padding(ranges, duration, part.get("pad", True))

        encoded_paths: list[str] = []
        durations: list[float] = []
        timed: list[captions.TimedSegment] = []
        for j, r in enumerate(padded):
            encoded = os.path.join(work_dir, f"part_{source_idx:03d}_{j:03d}.mp4")
            editor.extract_encoded(source_path, r.start, r.end, encoded, vf)
            encoded_paths.append(encoded)
            d = editor.get_duration(encoded)
            durations.append(d)
            text = kept_segments[j].text if j < len(kept_segments) else ""
            timed.append(captions.TimedSegment(start=0.0, end=d, text=text))

        os.remove(source_path)
        return _ProcessedPart(
            encoded_paths=encoded_paths, durations=durations, timed_segments=timed
        )
    except Exception as exc:
        raise ValueError(f"Failed to process part[{source_idx}] ({label}): {exc}") from exc


def stitch_clips_tool(
    parts: list[dict],
    output_name: str = "narrative.mp4",
    captions_enabled: bool = False,
    music: Optional[dict] = None,
    aspect: str = "16:9",
    frame_speaker: str = "none",
) -> dict:
    """Stitch segments from multiple source clips into one narrative video.

    Each part is a dict with:
        clip_file_id (str)        — Drive file ID of the .mp4
        transcript_file_id (str)  — Drive file ID of the .txt
        keep_segments (list[int]) — segment indices to keep, OR
        keep_ranges (list[dict])  — [{"start": "00:10.0", "end": "00:25.0"}, ...]
        pad (bool, default True)  — ±150/250ms padding
        label (str, optional)     — surfaced in error messages

    aspect (str): "9:16" (vertical, TikTok/Reels), "1:1" (square), "4:5"
        (Instagram portrait), or "16:9" (default, widescreen). Friendly
        aliases accepted ("vertical", "tiktok", "square", "instagram", etc.).

    frame_speaker (str): "right" or "left" crops + pans onto that panel of
        the source (e.g. for cal.com side-by-side recordings where the
        student is on the right). "none" (default) letterboxes/pillarboxes
        to preserve the whole frame.

    captions_enabled (bool): burn 3-word white-on-black-outline captions
        keyed to the transcript text of each kept segment.

    music (dict, optional): score the narrative. Shape:
        {
            "rising_action_through_part": int (required) — last part index
                belonging to the struggle phase (0-indexed, inclusive).
                Pass len(parts) - 1 to omit the triumph phase, or -1 to
                skip rising-action.
            "pause_seconds": float (default 2.0) — turning-point silence
            "music_volume": float (default 0.22) — relative to voice
        }
    """
    if not parts:
        raise ValueError("Provide at least one part to stitch")

    output_name = os.path.basename(output_name) or "narrative.mp4"
    if not output_name.lower().endswith(".mp4"):
        output_name = output_name + ".mp4"

    aspect_canonical, out_w, out_h = editor.resolve_aspect(aspect)
    vf = editor.build_video_filter(out_w, out_h, frame_speaker)

    metas: list[drive.DriveFile] = []
    for i, part in enumerate(parts):
        clip_id = part.get("clip_file_id")
        if not clip_id:
            raise ValueError(f"part[{i}] missing clip_file_id")
        meta = drive.get_metadata(clip_id)
        if meta.size is not None and meta.size > MAX_INPUT_BYTES:
            raise ValueError(
                f"part[{i}] ({meta.name}) is "
                f"{meta.size / 1024 / 1024:.1f} MB, over the "
                f"{MAX_INPUT_BYTES // 1024 // 1024} MB limit"
            )
        metas.append(meta)

    token, work_dir = downloads.make_workspace()

    processed: list[_ProcessedPart] = []
    for i, part in enumerate(parts):
        processed.append(_process_part(part, work_dir, i, metas, vf))

    total_kept = sum(len(p.encoded_paths) for p in processed)
    if total_kept == 0:
        raise ValueError("No segments were kept across all parts")

    # ---------- Assemble video timeline, inserting turning-point pause if scoring ----------

    pause_seconds = 0.0
    rising_through = None
    if music:
        rising_through = int(music.get("rising_action_through_part", -1))
        pause_seconds = float(music.get("pause_seconds", 2.0))

    pause_index = -1  # index into the flat list of encoded parts where the pause goes
    pause_path: Optional[str] = None
    if music and 0 <= rising_through < len(parts) - 1 and pause_seconds > 0:
        # Pause sits between rising_through's last segment and (rising_through+1)'s first.
        pause_index = sum(len(p.encoded_paths) for p in processed[: rising_through + 1])
        pause_path = os.path.join(work_dir, "pause.mp4")
        editor.generate_silent_black(pause_seconds, pause_path, out_w, out_h)

    # Flatten all encoded parts in order, optionally inserting the pause.
    flat_parts: list[str] = []
    flat_durations: list[float] = []
    for p in processed:
        flat_parts.extend(p.encoded_paths)
        flat_durations.extend(p.durations)
    if pause_path is not None:
        flat_parts.insert(pause_index, pause_path)
        flat_durations.insert(pause_index, pause_seconds)

    # Compute output-relative timing for each sub-clip (used for captions + music layout).
    cursor = 0.0
    timeline_offsets: list[float] = []
    for d in flat_durations:
        timeline_offsets.append(cursor)
        cursor += d
    total_duration = cursor

    # ---------- Concat into a single mp4 ----------

    stitched_path = os.path.join(work_dir, "stitched.mp4")
    editor.concat_parts(flat_parts, stitched_path, work_dir)
    current_video = stitched_path

    # ---------- Mix in music if requested ----------

    if music:
        rising_duration = 0.0
        triumph_duration = 0.0
        if rising_through is not None and rising_through >= 0:
            rising_duration = sum(
                sum(p.durations) for p in processed[: rising_through + 1]
            )
        if rising_through is not None and rising_through < len(parts) - 1:
            triumph_duration = sum(
                sum(p.durations) for p in processed[rising_through + 1 :]
            )
        # If only one phase was requested, the music covers that phase only.
        layout = score.ScoreLayout(
            rising_duration=rising_duration,
            pause_duration=pause_seconds if pause_index >= 0 else 0.0,
            triumph_duration=triumph_duration,
        )
        score_path = os.path.join(work_dir, "score.m4a")
        score.build_score_track(layout, score_path, work_dir)

        mixed_path = os.path.join(work_dir, "mixed.mp4")
        editor.mix_music_with_voice(
            current_video,
            score_path,
            mixed_path,
            music_volume=float(music.get("music_volume", 0.22)),
        )
        current_video = mixed_path

    # ---------- Burn captions if requested ----------

    if captions_enabled:
        # Build a flat list of timed segments mapped onto the output timeline.
        all_timed: list[captions.TimedSegment] = []
        idx = 0
        for p in processed:
            for seg in p.timed_segments:
                # If a pause sits before this index in the flat list, the offset already accounts for it.
                offset = timeline_offsets[idx]
                all_timed.append(
                    captions.TimedSegment(
                        start=offset, end=offset + seg.end, text=seg.text
                    )
                )
                idx += 1
            # Skip the pause's offset slot if we just crossed it.
            if pause_index >= 0 and idx == pause_index:
                idx += 1

        ass_text = captions.build_ass(all_timed, video_width=out_w, video_height=out_h)
        ass_path = os.path.join(work_dir, "captions.ass")
        with open(ass_path, "w", encoding="utf-8") as fh:
            fh.write(ass_text)

        burned_path = os.path.join(work_dir, "burned.mp4")
        editor.burn_captions(current_video, ass_path, burned_path)
        current_video = burned_path

    # ---------- Finalize ----------

    output_path = os.path.join(work_dir, output_name)
    if current_video != output_path:
        os.rename(current_video, output_path)

    # Clean up intermediates — keep only the final mp4.
    for f in os.listdir(work_dir):
        full = os.path.join(work_dir, f)
        if os.path.isfile(full) and full != output_path:
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
        "parts_count": len(parts),
        "total_segments_kept": total_kept,
        "aspect": aspect_canonical,
        "resolution": f"{out_w}x{out_h}",
        "frame_speaker": frame_speaker,
        "captions": captions_enabled,
        "music_scored": music is not None,
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
