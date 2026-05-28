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

mcp = FastMCP("clip-editor")


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
def list_clips(student_folder_id: str) -> dict:
    """List all clips and transcripts for a student across event types."""
    return tools.list_clips_tool(student_folder_id)


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
