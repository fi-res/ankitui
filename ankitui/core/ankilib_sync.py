"""Sync through the official ``anki`` Python library (pylib), when it is installed.

The library is Anki's own backend (the Rust core shared with Anki desktop and
AnkiDroid). For a sync, AnkiTUI's schema 11 collection is opened with it, Anki
performs the sync, and the file is downgraded back to schema 11 on close.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .sync import SyncError

Progress = Callable[[str], None]

# values of anki.sync_pb2.SyncCollectionResponse.ChangesRequired
NO_CHANGES = 0
NORMAL_SYNC = 1
FULL_SYNC = 2
FULL_DOWNLOAD = 3
FULL_UPLOAD = 4


def available() -> bool:
    try:
        import anki.collection  # noqa: F401
    except Exception:
        return False
    return True


def _error(err: Exception) -> SyncError:
    """Map pylib errors to SyncError kinds."""
    try:
        from anki.errors import NetworkError, SyncError as AnkiSyncError, SyncErrorKind
    except Exception:  # pragma: no cover
        return SyncError(str(err))
    if isinstance(err, AnkiSyncError):
        kind = "auth" if err.kind == SyncErrorKind.AUTH else "server"
        return SyncError(str(err) or kind, kind)
    if isinstance(err, NetworkError):
        return SyncError(f"Network error: {err}", "network")
    return SyncError(f"{type(err).__name__}: {err}")


def login(username: str, password: str, endpoint: str | None) -> tuple[str, str | None]:
    """Log in with Anki's own code. Returns (host key, endpoint)."""
    import tempfile

    from anki.collection import Collection

    with tempfile.TemporaryDirectory(prefix="ankitui-login-") as tmp:
        col = Collection(f"{tmp}/collection.anki2")
        try:
            auth = col.sync_login(username, password, endpoint)
        except Exception as err:
            raise _error(err) from err
        finally:
            col.close()
    return auth.hkey, (auth.endpoint or endpoint or None)


@dataclass
class LibResult:
    action: str  # "no_changes", "normal", "full_required"
    server_message: str = ""
    new_endpoint: str | None = None
    upload_ok: bool = True
    download_ok: bool = True
    media_usn: int | None = None
    local_empty: bool = False
    remote_empty: bool = False


class AnkiLibSession:
    """An open pylib collection for the duration of one sync. Call close() at the end."""

    def __init__(self, path: str, hkey: str, endpoint: str | None, progress: Progress | None = None):
        from anki.collection import Collection
        from anki.sync_pb2 import SyncAuth

        self.progress = progress or (lambda _m: None)
        self.progress("Opening collection with the Anki library...")
        try:
            self.col = Collection(path)
        except Exception as err:
            raise _error(err) from err
        self.auth = SyncAuth(hkey=hkey)
        if endpoint:
            self.auth.endpoint = endpoint

    def sync_collection(self) -> LibResult:
        self.progress("Syncing collection...")
        try:
            out = self.col.sync_collection(self.auth, False)
        except Exception as err:
            raise _error(err) from err
        result = LibResult("no_changes", out.server_message, out.new_endpoint or None, media_usn=out.server_media_usn)
        if out.new_endpoint:
            self.auth.endpoint = out.new_endpoint
        if out.required == NORMAL_SYNC:
            result.action = "normal"
        elif out.required in (FULL_SYNC, FULL_DOWNLOAD, FULL_UPLOAD):
            result.action = "full_required"
            result.upload_ok = out.required in (FULL_SYNC, FULL_UPLOAD)
            result.download_ok = out.required in (FULL_SYNC, FULL_DOWNLOAD)
            result.local_empty = out.required == FULL_DOWNLOAD
            result.remote_empty = out.required == FULL_UPLOAD
        return result

    def full_sync(self, upload: bool, media_usn: int | None) -> None:
        self.progress("Uploading collection..." if upload else "Downloading collection...")
        try:
            self.col.close_for_full_sync()
            self.col.full_upload_or_download(auth=self.auth, server_usn=media_usn, upload=upload)
        except Exception as err:
            raise _error(err) from err
        finally:
            self.col.reopen(after_full_sync=True)

    def sync_media(self) -> None:
        self.progress("Syncing media...")
        try:
            self.col.sync_media(self.auth)
            while True:
                status = self.col.media_sync_status()
                if not status.active:
                    break
                p = status.progress
                if p.checked or p.added or p.removed:
                    self.progress(f"Media: checked {p.checked}, added {p.added}, removed {p.removed}")
                time.sleep(0.3)
        except Exception as err:
            raise _error(err) from err

    def close(self) -> None:
        """Close the collection, converting it back to schema 11 for AnkiTUI."""
        if self.col.db is not None:
            self.col.close(downgrade=True)
