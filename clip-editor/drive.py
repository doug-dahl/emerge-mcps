"""Google Drive API wrapper using a service account.

Reuses the `enrollment-clip-uploader` service account that's already a Manager
on the Student Interviews shared drive (Google Secret Manager: `DRIVE_SA_KEY`).
The secret is base64-encoded JSON — same format used by functions/clients/drive.

All calls pass `supportsAllDrives=True` and `includeItemsFromAllDrives=True`
because the corpus lives on a shared drive.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import os
from dataclasses import dataclass
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(RuntimeError):
    pass


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    size: Optional[int]
    parents: Optional[list[str]] = None


def _load_credentials() -> service_account.Credentials:
    """Load the SA key from env. Accepts base64-encoded JSON (Railway/Secret Manager
    format) or raw JSON (local dev convenience). Tries base64 first since that
    matches the existing `DRIVE_SA_KEY` secret in Google Secret Manager.
    """
    raw = os.environ.get("DRIVE_SA_KEY") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise DriveError("DRIVE_SA_KEY env var is not set")

    info: Optional[dict] = None
    # Try base64 first (matches functions/clients/drive/client.ts).
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        info = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        pass

    # Fall back to raw JSON.
    if info is None:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DriveError(
                "DRIVE_SA_KEY is neither base64-encoded JSON nor raw JSON"
            ) from exc

    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


_service = None


def get_service():
    """Lazily build and cache the Drive client."""
    global _service
    if _service is None:
        _service = build("drive", "v3", credentials=_load_credentials(), cache_discovery=False)
    return _service


def get_metadata(file_id: str) -> DriveFile:
    try:
        meta = (
            get_service()
            .files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,size,parents",
                supportsAllDrives=True,
            )
            .execute()
        )
    except HttpError as exc:
        raise DriveError(f"Drive metadata fetch failed for {file_id}: {exc}") from exc
    return DriveFile(
        id=meta["id"],
        name=meta["name"],
        mime_type=meta.get("mimeType", ""),
        size=int(meta["size"]) if "size" in meta else None,
        parents=meta.get("parents"),
    )


def download_file(file_id: str, dest_path: str) -> DriveFile:
    """Stream-download a binary file to dest_path."""
    meta = get_metadata(file_id)
    try:
        request = get_service().files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
    except HttpError as exc:
        raise DriveError(f"Drive download failed for {file_id}: {exc}") from exc
    return meta


def read_text_file(file_id: str) -> str:
    """Read a text file's contents into memory (for transcripts — typically small)."""
    try:
        request = get_service().files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except HttpError as exc:
        raise DriveError(f"Drive text read failed for {file_id}: {exc}") from exc
    return buf.getvalue().decode("utf-8", errors="replace")


def list_folder(folder_id: str) -> list[DriveFile]:
    """List immediate children of a folder."""
    results: list[DriveFile] = []
    page_token: Optional[str] = None
    try:
        while True:
            resp = (
                get_service()
                .files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, size)",
                    pageSize=200,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resp.get("files", []):
                results.append(
                    DriveFile(
                        id=f["id"],
                        name=f["name"],
                        mime_type=f.get("mimeType", ""),
                        size=int(f["size"]) if "size" in f else None,
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        raise DriveError(f"Drive list failed for folder {folder_id}: {exc}") from exc
    return results


def list_folders(folder_ids: list[str]) -> dict[str, list[DriveFile]]:
    """List immediate children of several folders in ONE Drive query.

    Returns {folder_id: [children]} (folders with no children are absent).
    Round trips matter: on a slow link each Drive call can take 3-6 s, and
    Claude Desktop gives up on a tool call after about a minute, so one
    query for N folders instead of N queries is the difference between
    list_clips working and timing out.
    """
    folder_ids = [fid for fid in dict.fromkeys(folder_ids) if fid]
    results: dict[str, list[DriveFile]] = {}
    if not folder_ids:
        return results
    wanted = set(folder_ids)
    parents_q = " or ".join(f"'{fid}' in parents" for fid in folder_ids)
    # corpora="allDrives" is REQUIRED for a multi-parent OR query on a shared
    # drive. With the default corpora Drive silently returns a fraction of the
    # files (8 of 61 in testing) while still reporting incompleteSearch=false.
    page_token: Optional[str] = None
    try:
        while True:
            resp = (
                get_service()
                .files()
                .list(
                    q=f"({parents_q}) and trashed = false",
                    fields="nextPageToken, incompleteSearch, files(id, name, mimeType, size, parents)",
                    pageSize=200,
                    pageToken=page_token,
                    corpora="allDrives",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            if resp.get("incompleteSearch") and len(folder_ids) > 1:
                # Drive couldn't search everything in one go — fall back to
                # the slow-but-exact one-query-per-folder path.
                return {fid: list_folder(fid) for fid in folder_ids}
            for f in resp.get("files", []):
                df = DriveFile(
                    id=f["id"],
                    name=f["name"],
                    mime_type=f.get("mimeType", ""),
                    size=int(f["size"]) if "size" in f else None,
                    parents=f.get("parents"),
                )
                for parent in df.parents or []:
                    if parent in wanted:
                        results.setdefault(parent, []).append(df)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        raise DriveError(f"Drive list failed for folder(s) {', '.join(folder_ids)}: {exc}") from exc
    return results
