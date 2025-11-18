import asyncio
import json
import logging
import os
import socket
import ssl
import time
from functools import lru_cache
from typing import Dict, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]
MAX_SHARE_ATTEMPTS = 3

SERVICE_ACCOUNT_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH")
DELEGATED_USER = os.environ.get("GOOGLE_DELEGATED_USER")
_DEFAULT_FOLDER_ID = os.environ.get("DEFAULT_DRIVE_FOLDER")

raw_team_map = os.environ.get("TEAM_FOLDER_MAP", "")
TEAM_FOLDER_MAP: Dict[str, str] = {}
if raw_team_map:
    try:
        TEAM_FOLDER_MAP = json.loads(raw_team_map)
    except json.JSONDecodeError:
        for pair in raw_team_map.split(";"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                TEAM_FOLDER_MAP[key.strip()] = value.strip()


def _ensure_credentials() -> Credentials:
    if not SERVICE_ACCOUNT_PATH:
        raise RuntimeError("GOOGLE_CREDENTIALS_PATH must be set to a service-account JSON file")
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        raise FileNotFoundError(f"Google service account file not found: {SERVICE_ACCOUNT_PATH}")
    return Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH,
        scopes=SCOPES,
        subject=DELEGATED_USER,
    )


@lru_cache(maxsize=1)
def _get_drive_service():
    credentials = _ensure_credentials()
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _folder_for_team(team: str) -> str:
    if team in TEAM_FOLDER_MAP:
        return TEAM_FOLDER_MAP[team]
    if _DEFAULT_FOLDER_ID:
        return _DEFAULT_FOLDER_ID
    raise ValueError(f"No Google Drive folder configured for team '{team}'")


class ShareFailure(Exception):
    def __init__(self, user_message: str, original: Exception):
        super().__init__(str(original))
        self.user_message = user_message
        self.original = original


def _get_http_status(exc: HttpError) -> Optional[int]:
    if exc.resp is not None and hasattr(exc.resp, "status"):
        try:
            return int(exc.resp.status)
        except (ValueError, TypeError):
            return None
    return None


def _is_network_error(exc: Exception) -> bool:
    network_errors = (ssl.SSLError, socket.timeout, ConnectionResetError)
    return isinstance(exc, network_errors) or (
        isinstance(exc, OSError) and "timed out" in str(exc).lower()
    )


def _map_user_message(exc: Exception) -> str:
    if isinstance(exc, HttpError):
        status = _get_http_status(exc)
        if status in (400, 404):
            return "الإيميل ده مش موجود أو مش شغال على Google، جرب إيميل تاني."
        if status == 403:
            return "الإيميل ده ما عندهوش صلاحية للوصول أو المجلد مقفل، جرب إيميل تاني."
    if _is_network_error(exc):
        return "النت مش ثابت دلوقتي، جرب بعد شوية."
    return "حصل خطأ غير متوقع أثناء مشاركة المجلد، تواصل مع الأدمن لو المشكلة مستمرة."


def _share_folder_sync(team: str, email: str, role: str = "reader") -> str:
    folder_id = _folder_for_team(team)
    logging.info("Sharing folder %s with %s", folder_id, email)
    service = _get_drive_service()
    permission = {
        "type": "user",
        "role": role,
        "emailAddress": email,
    }
    for attempt in range(1, MAX_SHARE_ATTEMPTS + 1):
        try:
            service.permissions().create(
                fileId=folder_id,
                body=permission,
                sendNotificationEmail=False,
                supportsAllDrives=True,
            ).execute()
            logging.info("Folder %s shared with %s", folder_id, email)
            return folder_id
        except HttpError as exc:
            status = _get_http_status(exc)
            if status == 409:
                logging.info("Permission already exists for %s on folder %s", email, folder_id)
                return folder_id
            user_msg = _map_user_message(exc)
            if attempt == MAX_SHARE_ATTEMPTS:
                raise ShareFailure(user_msg, exc)
            logging.warning(
                "Retry %s/%s for sharing folder %s with %s (status=%s)",
                attempt,
                MAX_SHARE_ATTEMPTS,
                folder_id,
                email,
                status,
            )
        except Exception as exc:
            user_msg = _map_user_message(exc)
            if attempt == MAX_SHARE_ATTEMPTS:
                raise ShareFailure(user_msg, exc)
            logging.warning(
                "Retry %s/%s for sharing folder %s with %s because %s",
                attempt,
                MAX_SHARE_ATTEMPTS,
                folder_id,
                email,
                exc,
            )
        if attempt < MAX_SHARE_ATTEMPTS:
            time.sleep(min(attempt * 1.5, 5))
    return folder_id


def _list_files_sync(folder_id: str, page_size: int = 5) -> List[Dict[str, str]]:
    service = _get_drive_service()
    query = f"'{folder_id}' in parents and trashed=false"
    results = (
        service.files()
        .list(
            q=query,
            pageSize=page_size,
            orderBy="modifiedTime desc",
            fields="files(id,name,webViewLink)",
            supportsAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


async def list_files_for_team(team: str, page_size: int = 5) -> List[Dict[str, str]]:
    folder_id = _folder_for_team(team)
    return await asyncio.to_thread(_list_files_sync, folder_id, page_size)


def folder_url_for_team(team: str) -> str:
    return f"https://drive.google.com/drive/folders/{_folder_for_team(team)}"


async def share_folder_with_user(team: str, email: str, role: str = "reader") -> str:
    return await asyncio.to_thread(_share_folder_sync, team, email, role)
