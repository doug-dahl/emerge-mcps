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
    """Estimate cut duration without knowing file duration (preview-only).

    Mirrors the real render: pad each range, then coalesce contiguous ones so
    the estimate doesn't double-count the overlap between back-to-back segments.
    """
    padded = [
        editor.Range(
            max(0.0, r.start - (editor.PAD_PRE if pad else 0.0)),
            r.end + (editor.PAD_POST if pad else 0.0),
        )
        for r in ranges
    ]
    merged = editor.merge_ranges(padded)
    return sum(max(0.0, r.end - r.start) for r in merged)


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
class _MergedCut:
    """A single continuous cut after coalescing contiguous segments, plus the
    transcript segments it spans (in source-time) for caption timing.
    """

    start: float
    end: float
    members: list[tuple[float, float, str]]  # (seg_start, seg_end, text) in source time


@dataclass
class _ProcessedPart:
    """A part after extraction: the encoded sub-clips and the timed segments
    that those sub-clips correspond to. `timed_segments` is positioned relative
    to the START OF THIS PART; the stitch step adds each part's offset.
    """

    encoded_paths: list[str]
    durations: list[float]
    timed_segments: list[captions.TimedSegment]
    segment_count: int  # kept transcript segments (pre-merge), for reporting
    # [start, end] of this part's footage in ORIGINAL-recording time (clip
    # offset + kept range), used to detect overlap between adjacent parts cut
    # from the same interview. None when timing is unavailable.
    recording_window: Optional[tuple[float, float]]


def _merge_cuts(
    padded: list[editor.Range],
    kept_segments: list[transcript.Segment],
) -> list[_MergedCut]:
    """Coalesce contiguous padded ranges into single cuts (so an unbroken run of
    kept segments renders as one smooth clip instead of overlapping sub-clips
    that replay the padding), while remembering which segments each cut spans so
    captions can still be timed per-segment.
    """
    cuts: list[_MergedCut] = []
    for idx, r in enumerate(padded):
        seg = kept_segments[idx] if idx < len(kept_segments) else None
        seg_start = seg.start if seg is not None else r.start
        seg_end = seg.end if (seg is not None and seg.end is not None) else r.end
        text = seg.text if seg is not None else ""
        member = (seg_start, seg_end, text)
        if cuts and cuts[-1].start <= r.start <= cuts[-1].end:
            cuts[-1].end = max(cuts[-1].end, r.end)
            cuts[-1].members.append(member)
        else:
            cuts.append(_MergedCut(start=r.start, end=r.end, members=[member]))
    return cuts


def _process_part(
    part: dict,
    work_dir: str,
    source_idx: int,
    metas: list[drive.DriveFile],
    out_w: int,
    out_h: int,
    default_frame_speaker: str,
) -> _ProcessedPart:
    """Download a source mp4, cut + re-encode each kept range, collect timing info.

    Each part may set its own `frame_speaker` ("left"/"right"/"none") to crop
    onto the student's side of *that* clip — the student isn't always on the
    same side across different interviews. Falls back to the render-wide default.
    """
    label = part.get("label") or metas[source_idx].name
    try:
        clip_id = part["clip_file_id"]
        transcript_id = part.get("transcript_file_id")
        if not transcript_id:
            raise ValueError("missing transcript_file_id")

        side = part.get("frame_speaker") or default_frame_speaker
        vf = editor.build_video_filter(out_w, out_h, side)

        segments, offset, _ = _load_segments(transcript_id)
        keep_segments_arg = part.get("keep_segments")
        keep_ranges_arg = part.get("keep_ranges")
        ranges, kept_segments = _segments_to_ranges(
            segments, keep_segments_arg, keep_ranges_arg
        )

        source_path = os.path.join(work_dir, f"source_{source_idx:03d}.mp4")
        drive.download_file(clip_id, source_path)
        duration = editor.get_duration(source_path)
        padded = editor.apply_padding(ranges, duration, part.get("pad", True))
        cuts = _merge_cuts(padded, kept_segments)

        # This part's footage span in original-recording time, so the stitch
        # step can spot two parts (cut from the same interview) that overlap.
        recording_window = (
            offset + min(r.start for r in padded),
            offset + max(r.end for r in padded),
        )

        encoded_paths: list[str] = []
        durations: list[float] = []
        timed: list[captions.TimedSegment] = []
        part_cursor = 0.0  # seconds since the start of this part
        for j, cut in enumerate(cuts):
            encoded = os.path.join(work_dir, f"part_{source_idx:03d}_{j:03d}.mp4")
            editor.extract_encoded(source_path, cut.start, cut.end, encoded, vf)
            encoded_paths.append(encoded)
            d = editor.get_duration(encoded)
            durations.append(d)
            # Each spanned segment keeps its own caption window, offset within
            # the cut by how far into the cut the segment actually starts.
            for seg_start, seg_end, text in cut.members:
                rel_start = part_cursor + max(0.0, seg_start - cut.start)
                rel_end = part_cursor + min(d, seg_end - cut.start)
                timed.append(
                    captions.TimedSegment(start=rel_start, end=rel_end, text=text)
                )
            part_cursor += d

        os.remove(source_path)
        return _ProcessedPart(
            encoded_paths=encoded_paths,
            durations=durations,
            timed_segments=timed,
            segment_count=len(ranges),
            recording_window=recording_window,
        )
    except Exception as exc:
        raise ValueError(f"Failed to process part[{source_idx}] ({label}): {exc}") from exc


def _same_recording(a: drive.DriveFile, b: drive.DriveFile) -> bool:
    """True if two clips are the same interview: same file, or same Drive
    parent folder (each interview's clips live in one event folder)."""
    if a.id == b.id:
        return True
    if a.parents and b.parents:
        return bool(set(a.parents) & set(b.parents))
    return False


def _overlap_warnings(
    parts: list[dict],
    processed: list[_ProcessedPart],
    metas: list[drive.DriveFile],
) -> list[str]:
    """Flag adjacent parts whose footage overlaps in original-recording time.

    The dimension and highlight clips for one student are all cut from the same
    interview and overlap in the source recording. Placing two overlapping
    windows back-to-back replays the shared footage (a visible repeat), which
    segment indices alone don't reveal. We can't auto-trim without guessing at
    editorial intent, so we surface a warning instead.
    """
    warnings: list[str] = []
    for i in range(len(processed) - 1):
        wa = processed[i].recording_window
        wb = processed[i + 1].recording_window
        if wa is None or wb is None:
            continue
        if not _same_recording(metas[i], metas[i + 1]):
            continue  # different interviews — overlapping seconds are coincidence
        overlap = min(wa[1], wb[1]) - max(wa[0], wb[0])
        if overlap > 0.1:
            la = parts[i].get("label") or metas[i].name
            lb = parts[i + 1].get("label") or metas[i + 1].name
            warnings.append(
                f"Parts {i} ({la!r}) and {i + 1} ({lb!r}) are from the same "
                f"interview and overlap by {overlap:.1f}s in the source "
                f"recording — they will replay the same footage. Pick segments "
                f"from non-overlapping moments, or reorder so they aren't adjacent."
            )
    return warnings


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
        frame_speaker (str, optional) — "left"/"right"/"none" to crop onto the
            student's side of THIS clip; overrides the render-wide
            frame_speaker. Use when stitching clips from different interviews
            where the student sits on different sides.
        label (str, optional)     — surfaced in error messages

    aspect (str): "9:16" (vertical, TikTok/Reels), "1:1" (square), "4:5"
        (Instagram portrait), or "16:9" (default, widescreen). Friendly
        aliases accepted ("vertical", "tiktok", "square", "instagram", etc.).

    frame_speaker (str): render-wide default for cropping cal.com side-by-side
        recordings. "right"/"left" crops + pans onto that panel; "none"
        (default) letterboxes/pillarboxes to preserve the whole frame. The
        student is NOT always on the same side — verify per clip and set this
        (or the per-part override) accordingly. Each part may override it.

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
    # Validate the render-wide default up front (per-part overrides are
    # validated inside _process_part as each part is built).
    editor.build_video_filter(out_w, out_h, frame_speaker)

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
        processed.append(
            _process_part(part, work_dir, i, metas, out_w, out_h, frame_speaker)
        )

    total_cuts = sum(len(p.encoded_paths) for p in processed)
    total_kept = sum(p.segment_count for p in processed)
    if total_cuts == 0:
        raise ValueError("No segments were kept across all parts")

    warnings = _overlap_warnings(parts, processed, metas)
    for w in warnings:
        logger.warning(w)

    # ---------- Assemble video timeline ----------

    # Music phases (when scoring is requested):
    #   parts [0 .. rising_through]                — rising-action music
    #   parts [rising_through+1 .. triumph_from-1] — turning-point quote(s),
    #                                                voice only, no music
    #   parts [triumph_from .. end]                — triumph music
    rising_through = -1
    triumph_from = len(parts)  # default: no triumph phase
    if music:
        rising_through = int(music.get("rising_action_through_part", -1))
        triumph_from = int(
            music.get("triumph_from_part", rising_through + 1)
        )
        if triumph_from <= rising_through:
            raise ValueError(
                "music.triumph_from_part must be greater than "
                "music.rising_action_through_part"
            )

    flat_parts: list[str] = []
    flat_durations: list[float] = []
    for p in processed:
        flat_parts.extend(p.encoded_paths)
        flat_durations.extend(p.durations)

    total_duration = sum(flat_durations)

    # ---------- Concat into a single mp4 ----------

    stitched_path = os.path.join(work_dir, "stitched.mp4")
    editor.concat_parts(flat_parts, stitched_path, work_dir)
    current_video = stitched_path

    # ---------- Mix in music if requested ----------

    if music:
        rising_duration = sum(
            sum(p.durations)
            for p in processed[: max(0, rising_through + 1)]
        )
        turning_point_duration = sum(
            sum(p.durations)
            for p in processed[max(0, rising_through + 1) : triumph_from]
        )
        triumph_duration = sum(
            sum(p.durations) for p in processed[triumph_from:]
        )
        layout = score.ScoreLayout(
            rising_duration=rising_duration,
            pause_duration=turning_point_duration,  # silence under the turning-point quote(s)
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
        # Each part's timed_segments are already positioned relative to that
        # part's start; shift them by the cumulative duration of prior parts.
        all_timed: list[captions.TimedSegment] = []
        offset = 0.0
        for p in processed:
            for seg in p.timed_segments:
                all_timed.append(
                    captions.TimedSegment(
                        start=offset + seg.start, end=offset + seg.end, text=seg.text
                    )
                )
            offset += sum(p.durations)

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
        "warnings": warnings,
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
