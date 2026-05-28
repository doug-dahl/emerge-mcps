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
            .get(fileId=file_id, fields="id,name,mimeType,size", supportsAllDrives=True)
            .execute()
        )
    except HttpError as exc:
        raise DriveError(f"Drive metadata fetch failed for {file_id}: {exc}") from exc
    return DriveFile(
        id=meta["id"],
        name=meta["name"],
        mime_type=meta.get("mimeType", ""),
        size=int(meta["size"]) if "size" in meta else None,
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
