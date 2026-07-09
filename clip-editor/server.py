"""Entry point: mounts the MCP streamable-HTTP app alongside /health and /downloads."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route

import downloads
import tools

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("clip-editor")


def _transport_security() -> TransportSecuritySettings:
    """Allow the public Railway host through FastMCP's DNS-rebinding check.

    DNS-rebinding protection is meaningful for localhost services; it just
    blocks our deployment. Honor an ALLOWED_HOSTS allowlist when set, else
    disable the check for the public deployment.
    """
    raw = os.environ.get("ALLOWED_HOSTS", "").strip()
    if raw:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[h.strip() for h in raw.split(",") if h.strip()],
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


mcp = FastMCP("clip-editor", transport_security=_transport_security())


@mcp.tool()
def parse_transcript(file_id: str) -> dict:
    """Read a transcript .txt from Drive and return structured segments."""
    return tools.parse_transcript_tool(file_id)


@mcp.tool()
def preview_edit(
    file_id: str,
    keep_segments: Optional[list[int]] = None,
    keep_ranges: Optional[list[dict]] = None,
    pad: bool = True,
) -> dict:
    """Show what an edit would produce without downloading or cutting anything.

    Provide either keep_segments (list of segment indices) or keep_ranges
    (list of {start, end} timestamp strings).
    """
    return tools.preview_edit_tool(file_id, keep_segments, keep_ranges, pad)


@mcp.tool()
def edit_clip(
    clip_file_id: str,
    transcript_file_id: str,
    keep_segments: Optional[list[int]] = None,
    keep_ranges: Optional[list[dict]] = None,
    output_name: str = "edited_clip.mp4",
    pad: bool = True,
) -> dict:
    """Download the mp4 from Drive, cut it, and return a download URL."""
    return tools.edit_clip_tool(
        clip_file_id, transcript_file_id, keep_segments, keep_ranges, output_name, pad
    )


@mcp.tool()
def trim_silence(
    clip_file_id: str,
    output_name: str = "trimmed_clip.mp4",
    threshold_db: Optional[float] = None,
    peak_offset_db: float = 28.0,
    min_silence: float = 0.5,
    keep_pad: float = 0.1,
    min_clip: float = 0.0,
) -> dict:
    """Trim silent gaps / dead air from a clip using its audio levels — no transcript.

    Mimics a video editor's "trim silence" / auto-cut (the Auto-Editor model):
    detect quiet spans from the waveform and remove them, leaving a short cushion
    of silence so speech doesn't start or end abruptly. Re-renders only the
    audible spans in one frame-accurate pass and returns a download URL.

    threshold_db (float, optional): absolute audio level below which a span
        counts as silence. Leave unset (default) to use an ADAPTIVE threshold
        measured relative to the clip's own peak loudness — this is preferred
        because it adapts to how hot/quiet the recording was. Pass a value
        (e.g. -30) to force an absolute cut line.
    peak_offset_db (float, default 28.0): used only in adaptive mode — the
        silence line is set this many dB below the measured peak. Larger is
        stricter (cuts only near-total silence); smaller trims quieter audio
        too. (Adapts to recording level, not noise floor — a very noisy room can
        still leave dead air above the line.)
    min_silence (float, default 0.5): only gaps at least this many seconds long
        are removed, so natural beats between sentences survive.
    keep_pad (float, default 0.1): seconds of silence left on each side of every
        kept span. Larger values sound more relaxed; 0 cuts tight to the speech.
    min_clip (float, default 0.0): drop kept spans shorter than this many seconds
        (filters out an isolated word or click between two long pauses). 0 keeps
        everything audible.

    Returns include threshold_mode ("adaptive"/"absolute"), the threshold_db
    actually used, and the measured peak_db, so you can re-tune in one pass.
    """
    return tools.trim_silence_tool(
        clip_file_id, output_name, threshold_db, peak_offset_db,
        min_silence, keep_pad, min_clip,
    )


@mcp.tool()
def list_clips(student_folder_id: str) -> dict:
    """List all clips and transcripts for a student across event types."""
    return tools.list_clips_tool(student_folder_id)


@mcp.tool()
def stitch_clips(
    parts: list[dict],
    output_name: str = "narrative.mp4",
    captions: bool = False,
    music: Optional[dict] = None,
    aspect: str = "16:9",
    frame_speaker: str = "none",
    music_bed: Optional[str] = None,
    music_bed_volume: float = 0.25,
    music_bed_start: float = 0.0,
    intro: Optional[dict] = None,
    outro: Optional[dict] = None,
) -> dict:
    """Stitch segments from multiple source clips into one narrative video.

    Each entry in `parts` is a dict:
        {
            "clip_file_id": "<Drive .mp4 ID>",
            "transcript_file_id": "<Drive .txt ID>",
            "keep_segments": [0, 2, 5],          # OR keep_ranges
            "keep_ranges": [{"start": "00:10.0", "end": "00:25.0"}],
            "header": "Michael Dimick",                     # optional name/title chyron (first ~3s of this part)
            "subheader": "U.S. Army Veteran · Lynn, MA",    # optional 2nd chyron line
            "pad": true,
            "frame_speaker": "left",              # optional per-part override
            "label": "Roy - Stability"            # optional, used in errors
        }

    Re-encodes each kept range to a common H.264/AAC format so the final concat
    works across disparate source recordings.

    aspect (str, default "16:9"): output canvas aspect. One of
        "9:16" (vertical, 1080x1920 — TikTok/Reels),
        "1:1"  (square, 1080x1080 — Instagram feed),
        "4:5"  (portrait, 1080x1350 — Instagram portrait),
        "16:9" (widescreen, 1280x720 — default).
        Friendly aliases accepted: "vertical", "tiktok", "reels", "square",
        "instagram", "horizontal", etc.

    frame_speaker (str, default "none"): render-wide default for cropping
        cal.com side-by-side recordings onto one panel:
        "right" — frame the participant on the right
        "left"  — frame the participant on the left
        "none"  — letterbox/pillarbox to preserve the whole frame
        The student is NOT always on the same side — the panel order varies per
        recording, so verify (e.g. a "none" preview) before choosing. Each part
        can override this with its own "frame_speaker" key, which is essential
        when stitching clips from different interviews where the student sits on
        different sides. When aspect changes (e.g. 16:9 -> 9:16) cropping gives
        a portrait of just that person instead of a letterboxed full recording.

    captions (bool): burn lowercase white-on-black-outline captions (3 words
        per line, TikTok-style) derived from each kept segment's transcript text.
        Font size scales with output height so captions read the same on any
        canvas.

    music (dict, optional): score the narrative with two-act music with a
        voice-only turning point in the middle. Shape:
            {
                "rising_action_through_part": 1,   # last part with rising-action
                                                   #   music (0-indexed, inclusive)
                "triumph_from_part": 3,            # first part with triumph music
                                                   #   (parts in between play with
                                                   #   no backing track — that's the
                                                   #   turning-point quote)
                "music_volume": 0.22               # relative to voice (default 0.22)
            }
        Music phases:
            parts [0 .. rising_action_through_part]   — rising-action backing
            parts [..   .. triumph_from_part - 1]     — turning-point quote(s),
                                                         voice only, no music
            parts [triumph_from_part .. end]          — triumph backing
        If triumph_from_part == rising_action_through_part + 1, the music
        switches instantly (no turning-point gap).
        The default score tracks are royalty-free (Kevin MacLeod, CC BY 4.0);
        see clip-editor/assets/music/CREDITS.md.

    music_bed (str, optional): lay ONE track as a single continuous bed under
        the whole video (gentle fade in/out) instead of the two-act `music`
        score — the better fit for sensitive/documentary stories. Accepts a
        bundled royalty-free bed name — "hopeful", "calm", "cinematic",
        "uplifting" — or a path to your own audio file. Mutually exclusive with
        `music`.
    music_bed_volume (float, default 0.25): bed level relative to the voice.
    music_bed_start (float, default 0.0): seconds to skip into the track (to
        avoid a soft/near-silent intro).

    intro / outro (dict, optional): a branded title / closing slate before /
        after the video — a solid card with centered text and the Emerge logo,
        with a gentle fade. Shape:
            {
                "title": "He served in the U.S. Army.",   # required; "\\n" for a line break
                "subtitle": "After 9/11, he worked Ground Zero.",  # optional
                "seconds": 4.0,                            # optional hold time
                "logo": true                               # optional, show Emerge logo (default)
            }
        With `music_bed`, the bed plays continuously under the slates too — the
        recipe for a polished, branded funder/testimony piece.
    """
    return tools.stitch_clips_tool(
        parts, output_name, captions, music, aspect, frame_speaker,
        music_bed=music_bed,
        music_bed_volume=music_bed_volume,
        music_bed_start=music_bed_start,
        intro=intro,
        outro=outro,
    )


async def health(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def download(request: Request) -> Response:
    token = request.path_params["token"]
    filename = request.path_params["filename"]
    path = downloads.resolve(token, filename)
    if path is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def build_app() -> Starlette:
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        cleanup_task = asyncio.create_task(downloads.cleanup_loop())
        try:
            async with mcp_app.router.lifespan_context(app):
                yield
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except (asyncio.CancelledError, Exception):
                pass

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/downloads/{token}/{filename}", download, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
    )


app = build_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
