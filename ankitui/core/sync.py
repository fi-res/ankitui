"""AnkiWeb synchronisation (sync protocol v11), ported from rslib/src/sync.

Covers login (host key), normal (incremental) sync, full upload/download and
media sync. The transport is plain HTTPS POST with zstd-compressed JSON bodies
and an ``anki-sync`` header, exactly like Anki desktop and AnkiDroid.
"""

from __future__ import annotations

import io
import json
import os
import platform
import random
import shutil
import sqlite3
import string
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import zstandard

from .. import __version__
from . import consts
from .models import CARD_COLUMNS
from .utils import field_checksum, strip_html_media

if TYPE_CHECKING:
    from .collection import Collection

DEFAULT_ENDPOINT = "https://sync.ankiweb.net/"
SYNC_VERSION = 11
CHUNK_SIZE = 250
MAX_UPLOAD_BYTES = 100 * 1024 * 1024 * 3
CLIENT_VERSION = os.environ.get(
    "ANKITUI_SYNC_CLIENT_VERSION", f"ankitui,{__version__} (ankitui),{platform.system().lower() or 'linux'}"
)
CLIENT_VERSION_SHORT = f"{__version__},ankitui,{platform.system().lower() or 'linux'}"

Progress = Callable[[str], None]


class SyncError(Exception):
    """A sync failure. kind is one of: auth, server, network, clock, sanity, resync, other."""

    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


class _Redirect(Exception):
    def __init__(self, location: str):
        super().__init__(location)
        self.location = location


def _session_key() -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))


class HttpSyncClient:
    def __init__(self, hkey: str = "", endpoint: str | None = None, timeout: float = 60.0):
        self.hkey = hkey
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        if not self.endpoint.endswith("/"):
            self.endpoint += "/"
        self.skey = _session_key()
        self.timeout = timeout

    def _url(self, method: str) -> str:
        return urllib.parse.urljoin(self.endpoint, method)

    def request(self, method: str, payload: bytes, timeout: float | None = None) -> bytes:
        """POST a zstd-compressed payload to e.g. 'sync/meta' and return the decoded body."""
        header = json.dumps({"v": SYNC_VERSION, "k": self.hkey, "c": CLIENT_VERSION_SHORT, "s": self.skey})
        req = urllib.request.Request(
            self._url(method),
            data=zstandard.ZstdCompressor().compress(payload),
            method="POST",
            headers={"anki-sync": header, "Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                data = resp.read()
                if resp.headers.get("anki-original-size") is not None:
                    data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
                return data
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace").strip()
            if err.code == 308:
                location = err.headers.get("Location")
                if location:
                    raise _Redirect(location) from err
            if err.code == 403:
                raise SyncError(body or "Authentication failed. Please log in again.", "auth") from err
            raise SyncError(f"Server error {err.code}: {body or err.reason}", "server") from err
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise SyncError(f"Network error: {reason}", "network") from err

    def json(self, method: str, obj, timeout: float | None = None):
        data = self.request(method, json.dumps(obj).encode("utf-8"), timeout)
        if not data:
            return None
        return json.loads(data)

    def media_json(self, method: str, obj):
        """Media endpoints wrap results as {"data": ..., "err": ""}."""
        result = self.json(method, obj)
        if isinstance(result, dict) and result.get("err"):
            raise SyncError(result["err"], "server")
        return result.get("data") if isinstance(result, dict) else result


def login(username: str, password: str, endpoint: str | None = None) -> str:
    """Return the host key for an AnkiWeb (or self-hosted) account."""
    client = HttpSyncClient("", endpoint)
    try:
        resp = client.json("sync/hostKey", {"u": username, "p": password})
    except SyncError as err:
        if err.kind == "auth":
            raise SyncError("AnkiWeb ID or password was incorrect; please try again.", "auth") from err
        raise
    if not resp or "key" not in resp:
        raise SyncError("Unexpected response from the server.", "server")
    return resp["key"]


# --------------------------------------------------------------------- results


@dataclass
class SyncMeta:
    modified: int
    schema: int
    usn: int
    current_time: int
    message: str = ""
    should_continue: bool = True
    host_number: int = 0
    empty: bool = False
    media_usn: int = 0


@dataclass
class SyncResult:
    action: str  # "no_changes", "normal", "full_required", "uploaded", "downloaded"
    server_message: str = ""
    new_endpoint: str | None = None
    upload_ok: bool = True
    download_ok: bool = True
    media_usn: int | None = None
    local_empty: bool = False
    remote_empty: bool = False
    stats: dict = field(default_factory=dict)


# ----------------------------------------------------------- collection sync


class CollectionSyncer:
    def __init__(self, col: "Collection", client: HttpSyncClient, progress: Progress | None = None):
        self.col = col
        self.client = client
        self.progress = progress or (lambda _msg: None)
        self.stats = {"sent": 0, "received": 0}

    # ---------------------------------------------------------- metadata

    def local_meta(self) -> SyncMeta:
        col = self.col
        self._ensure_mod_covers_pending_changes()
        empty = col.db.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is None
        return SyncMeta(col.mod, col.scm, col.usn_, int(time.time()), empty=empty)

    def _ensure_mod_covers_pending_changes(self) -> None:
        """Make sure unsynced objects are noticed even if col.mod wasn't bumped."""
        col = self.col
        if col.mod > col.ls:
            return
        pending = any(
            col.db.execute(f"SELECT 1 FROM {t} WHERE usn = -1 LIMIT 1").fetchone()
            for t in ("cards", "notes", "revlog", "graves")
        )
        pending = pending or any(
            int(o.get("usn", 0)) == -1 for o in list(col.decks.values()) + list(col.models.values()) + list(col.dconf.values())
        )
        pending = pending or any(u == -1 for u in col.tags.values())
        if pending:
            col.mod = int(time.time() * 1000)
            col.save(bump_mod=False)

    def remote_meta(self) -> tuple[SyncMeta, str | None]:
        new_endpoint = None
        request = {"v": SYNC_VERSION, "cv": CLIENT_VERSION}
        try:
            resp = self.client.json("sync/meta", request)
        except _Redirect as redirect:
            new_endpoint = redirect.location
            self.client.endpoint = redirect.location
            resp = self.client.json("sync/meta", request)
        meta = SyncMeta(
            modified=int(resp["mod"]),
            schema=int(resp["scm"]),
            usn=int(resp["usn"]),
            current_time=int(resp["ts"]),
            message=resp.get("msg", ""),
            should_continue=bool(resp.get("cont", True)),
            host_number=int(resp.get("hostNum", 0)),
            empty=bool(resp.get("empty", False)),
            media_usn=int(resp.get("media_usn", 0)),
        )
        return meta, new_endpoint

    def sync(self) -> SyncResult:
        self.progress("Checking for changes...")
        local = self.local_meta()
        size = os.path.getsize(self.col.path)
        if "ankiweb" in self.client.endpoint and size > MAX_UPLOAD_BYTES:
            raise SyncError("The collection is too large to sync with AnkiWeb.", "other")
        remote, new_endpoint = self.remote_meta()
        if not remote.should_continue:
            raise SyncError(remote.message or "The server asked to stop syncing.", "server")
        if abs(remote.current_time - local.current_time) > 300:
            raise SyncError(
                "Your computer's clock is not set to the correct time. Please fix the clock and try again.",
                "clock",
            )
        result = SyncResult("no_changes", remote.message, new_endpoint, media_usn=remote.media_usn)
        result.local_empty, result.remote_empty = local.empty, remote.empty
        if remote.modified == local.modified:
            return result
        if remote.schema != local.schema:
            result.action = "full_required"
            result.upload_ok = not local.empty or remote.empty
            result.download_ok = not remote.empty or local.empty
            return result
        self.col.sched.clear_queues()
        self.col.sched.undo_stack.clear()
        self.col.db.commit()
        try:
            self._normal_sync(local, remote)
        except BaseException as err:
            self.col.db.rollback()
            self.col._load()
            try:
                self.client.json("sync/abort", {})
            except Exception:
                pass
            if isinstance(err, SyncError) and err.kind == "sanity":
                self.col.mark_schema_modified()
                self.col.save()
            raise
        result.action = "normal"
        result.stats = dict(self.stats)
        return result

    # ------------------------------------------------------- normal sync

    def _normal_sync(self, local: SyncMeta, remote: SyncMeta) -> None:
        server_usn = remote.usn
        local_is_newer = local.modified > remote.modified
        self.progress("Syncing deletions...")
        self._start_and_process_deletions(local.usn, server_usn, local_is_newer)
        self.progress("Syncing note types, decks and options...")
        self._process_unchunked_changes(server_usn, local_is_newer)
        self.progress("Receiving changes...")
        while True:
            chunk = self.client.json("sync/chunk", {})
            self._apply_chunk(chunk, server_usn)
            if chunk.get("done"):
                break
        self.progress("Sending changes...")
        self._send_chunks(server_usn)
        self.progress("Checking...")
        self._sanity_check()
        self.progress("Finishing...")
        new_mod = int(self.client.json("sync/finish", {}))
        col = self.col
        col.ls = new_mod
        col.mod = new_mod
        col.usn_ = server_usn + 1
        col.save(bump_mod=False)

    def _start_and_process_deletions(self, client_usn: int, server_usn: int, local_is_newer: bool) -> None:
        db = self.col.db
        remote = self.client.json("sync/start", {"minUsn": client_usn, "lnewer": local_is_newer})
        local = {"cards": [], "notes": [], "decks": []}
        kinds = {0: "cards", 1: "notes", 2: "decks"}
        for oid, kind in db.execute("SELECT oid, type FROM graves WHERE usn = -1"):
            if kind in kinds:
                local[kinds[kind]].append(oid)
        db.execute("UPDATE graves SET usn = ? WHERE usn = -1", (server_usn,))
        while any(local.values()):
            chunk = {"cards": [], "notes": [], "decks": []}
            limit = CHUNK_SIZE
            for key in ("cards", "notes", "decks"):
                while limit and local[key]:
                    chunk[key].append(local[key].pop())
                    limit -= 1
            self.stats["sent"] += sum(len(v) for v in chunk.values())
            self.client.json("sync/applyGraves", {"chunk": chunk})
        self._apply_graves(remote or {}, server_usn)

    def _apply_graves(self, graves: dict, usn: int) -> None:
        col = self.col
        db = col.db
        for nid in graves.get("notes", []):
            db.execute("DELETE FROM notes WHERE id = ?", (nid,))
            db.execute("INSERT INTO graves VALUES (?,?,?)", (usn, nid, 1))
        for cid in graves.get("cards", []):
            db.execute("DELETE FROM cards WHERE id = ?", (cid,))
            db.execute("INSERT INTO graves VALUES (?,?,?)", (usn, cid, 0))
        for did in graves.get("decks", []):
            col.decks.pop(str(did), None)
            db.execute("INSERT INTO graves VALUES (?,?,?)", (usn, did, 2))
        self.stats["received"] += sum(len(graves.get(k, [])) for k in ("notes", "cards", "decks"))

    def _process_unchunked_changes(self, server_usn: int, local_is_newer: bool) -> None:
        col = self.col

        def pending(objs: dict) -> list[dict]:
            out = []
            for obj in objs.values():
                if int(obj.get("usn", 0)) == -1:
                    obj["usn"] = server_usn
                    out.append(obj)
            return out

        tags = [t for t, u in col.tags.items() if u == -1]
        for t in tags:
            col.tags[t] = server_usn
        changes = {
            "models": pending(col.models),
            "decks": [pending(col.decks), pending(col.dconf)],
            "tags": tags,
        }
        if local_is_newer:
            changes["conf"] = col.conf
            changes["crt"] = col.crt
        self.stats["sent"] += len(changes["models"]) + len(changes["decks"][0]) + len(changes["decks"][1]) + len(tags)
        remote = self.client.json("sync/applyChanges", {"changes": changes})
        self._apply_changes(remote, server_usn)

    def _apply_changes(self, remote: dict, latest_usn: int) -> None:
        col = self.col
        for nt in remote.get("models", []):
            existing = col.models.get(str(nt["id"]))
            if existing is not None:
                if int(existing.get("mod", 0)) > int(nt.get("mod", 0)):
                    continue
                if len(existing["flds"]) != len(nt["flds"]) or len(existing["tmpls"]) != len(nt["tmpls"]):
                    raise SyncError("A note type was changed in an incompatible way; a full sync is required.", "resync")
            self._ensure_unique_name(col.models, nt, latest_usn)
            col.models[str(nt["id"])] = nt
        decks, dconf = remote.get("decks") or [[], []]
        for deck in decks:
            existing = col.decks.get(str(deck["id"]))
            if existing is not None and int(existing.get("mod", 0)) > int(deck.get("mod", 0)):
                continue
            self._ensure_unique_name(col.decks, deck, latest_usn, separator="+")
            col.decks[str(deck["id"])] = deck
        for conf in dconf:
            existing = col.dconf.get(str(conf["id"]))
            if existing is not None and int(existing.get("mod", 0)) > int(conf.get("mod", 0)):
                continue
            col.dconf[str(conf["id"])] = conf
        known = {t.lower() for t in col.tags}
        for tag in remote.get("tags", []):
            if tag.lower() not in known:
                col.tags[tag] = latest_usn
                known.add(tag.lower())
        if remote.get("crt"):
            col.crt = int(remote["crt"])
        if remote.get("conf") is not None:
            col.conf = dict(remote["conf"])
        self.stats["received"] += len(remote.get("models", [])) + len(decks) + len(dconf) + len(remote.get("tags", []))

    @staticmethod
    def _ensure_unique_name(objects: dict, obj: dict, usn: int, separator: str = "-") -> None:
        name = obj["name"]
        taken = {o["name"].lower() for k, o in objects.items() if k != str(obj["id"])}
        while name.lower() in taken:
            name += separator
        if name != obj["name"]:
            obj["name"] = name
            obj["usn"] = usn

    def _apply_chunk(self, chunk: dict, server_usn: int) -> None:
        db = self.col.db
        for entry in chunk.get("revlog", []):
            db.execute("INSERT OR IGNORE INTO revlog VALUES (?,?,?,?,?,?,?,?,?)", tuple(entry))
        for entry in chunk.get("cards", []):
            row = db.execute("SELECT usn, mod FROM cards WHERE id = ?", (entry[0],)).fetchone()
            if row is None or row[0] != -1 or row[1] < entry[4]:
                db.execute(f"INSERT OR REPLACE INTO cards ({CARD_COLUMNS}) VALUES ({','.join('?' * 18)})", tuple(entry))
        for entry in chunk.get("notes", []):
            nid, guid, mid, mod, usn, tags, flds = entry[:7]
            row = db.execute("SELECT usn, mod FROM notes WHERE id = ?", (nid,)).fetchone()
            if row is not None and row[0] == -1 and row[1] >= mod:
                continue
            nt = self.col.models.get(str(mid))
            if nt is None:
                raise SyncError("A note's note type is missing; a full sync is required.", "resync")
            fields = flds.split("\x1f")
            sortf = min(int(nt.get("sortf", 0)), len(fields) - 1)
            sfld = strip_html_media(fields[sortf]) if fields else ""
            csum = field_checksum(fields[0]) if fields else 0
            db.execute(
                "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (nid, guid, mid, mod, usn, tags, flds, sfld, csum, 0, ""),
            )
            known = {t.lower() for t in self.col.tags}
            for tag in tags.split():
                if tag.lower() not in known:
                    self.col.tags[tag] = server_usn
                    known.add(tag.lower())
        self.stats["received"] += sum(len(chunk.get(k, [])) for k in ("revlog", "cards", "notes"))

    def _send_chunks(self, server_usn: int) -> None:
        db = self.col.db
        ids = {
            t: [r[0] for r in db.execute(f"SELECT id FROM {t} WHERE usn = -1")] for t in ("revlog", "cards", "notes")
        }
        while True:
            chunk_ids = {"revlog": [], "cards": [], "notes": []}
            limit = CHUNK_SIZE
            while limit > 0:
                before = limit
                for t in ("revlog", "notes", "cards"):
                    if ids[t]:
                        chunk_ids[t].append(ids[t].pop())
                        limit -= 1
                if limit == before:
                    break
            done = limit > 0
            chunk: dict = {"done": done}
            for t, cids in chunk_ids.items():
                if not cids:
                    continue
                q = ",".join(str(int(i)) for i in cids)
                db.execute(f"UPDATE {t} SET usn = ? WHERE id IN ({q})", (server_usn,))
                if t == "revlog":
                    rows = db.execute(f"SELECT * FROM revlog WHERE id IN ({q})").fetchall()
                    chunk["revlog"] = [list(r) for r in rows]
                elif t == "cards":
                    rows = db.execute(f"SELECT {CARD_COLUMNS} FROM cards WHERE id IN ({q})").fetchall()
                    chunk["cards"] = [list(r) for r in rows]
                else:
                    rows = db.execute(
                        f"SELECT id, guid, mid, mod, usn, tags, flds FROM notes WHERE id IN ({q})"
                    ).fetchall()
                    chunk["notes"] = [list(r) + ["", "", 0, ""] for r in rows]
                self.stats["sent"] += len(cids)
            self.client.json("sync/applyChunk", {"chunk": chunk})
            if done:
                return

    def sanity_counts(self) -> list:
        col = self.col
        db = col.db
        for table in ("cards", "notes", "revlog", "graves"):
            if db.execute(f"SELECT 1 FROM {table} WHERE usn = -1 LIMIT 1").fetchone():
                raise SyncError(f"table had usn=-1: {table}", "other")

        def count(table: str) -> int:
            return db.execute(f"SELECT count() FROM {table}").fetchone()[0]

        return [
            [0, 0, 0],
            count("cards"),
            count("notes"),
            count("revlog"),
            count("graves"),
            len(col.models),
            len(col.decks),
            len(col.dconf),
        ]

    def _sanity_check(self) -> None:
        resp = self.client.json("sync/sanityCheck2", {"client": self.sanity_counts()})
        if not resp or resp.get("status") != "ok":
            raise SyncError(
                "The sanity check failed. A full sync is required to bring the collections back in step.\n"
                f"local: {resp.get('c') if resp else None}\nserver: {resp.get('s') if resp else None}",
                "sanity",
            )

    # -------------------------------------------------------- full sync

    def full_upload(self) -> None:
        col = self.col
        self.progress("Preparing upload...")
        db = col.db
        db.execute("DELETE FROM graves")
        for table in ("notes", "cards", "revlog"):
            db.execute(f"UPDATE {table} SET usn = 0 WHERE usn = -1")
        for objs in (col.decks, col.dconf, col.models):
            for obj in objs.values():
                if int(obj.get("usn", 0)) == -1:
                    obj["usn"] = 0
        for tag, usn in list(col.tags.items()):
            if usn == -1:
                col.tags[tag] = 0
        col.usn_ += 1
        col.scm = int(time.time() * 1000)
        col.ls = col.scm
        col.save(bump_mod=False)
        db.execute("VACUUM")
        with open(col.path, "rb") as f:
            data = f.read()
        if "ankiweb" in self.client.endpoint and len(data) > MAX_UPLOAD_BYTES:
            raise SyncError(f"The collection is too large to upload ({len(data) / 1048576:.1f} MB).", "other")
        self.progress(f"Uploading collection ({len(data) / 1048576:.1f} MB)...")
        resp = self.client.request("sync/upload", data, timeout=600)
        text = resp.decode("utf-8", errors="replace")
        if text != "OK":
            raise SyncError(text or "Upload failed.", "server")

    def full_download(self) -> None:
        """Replace the collection with the server's copy. The caller must reopen the collection."""
        from .schema18 import convert_to_schema11

        col = self.col
        self.progress("Downloading collection...")
        data = self.client.request("sync/download", b"{}", timeout=600)
        folder = os.path.dirname(col.path)
        tmp_dir = tempfile.mkdtemp(prefix="ankitui-download-", dir=folder)
        try:
            raw = os.path.join(tmp_dir, "download.anki2")
            with open(raw, "wb") as f:
                f.write(data)
            try:
                check = sqlite3.connect(raw)
                check.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
                ok = check.execute("PRAGMA integrity_check").fetchone()[0]
                check.close()
            except sqlite3.DatabaseError as err:
                raise SyncError(f"The downloaded collection is corrupt: {err}", "other") from err
            if ok != "ok":
                raise SyncError("The downloaded collection failed its integrity check.", "other")
            self.progress("Converting collection...")
            converted = os.path.join(tmp_dir, "converted.anki2")
            convert_to_schema11(raw, converted)
            conn = sqlite3.connect(converted)
            conn.execute("UPDATE col SET ls = mod")
            conn.commit()
            conn.close()
            col.db.close()
            col.db = None
            shutil.copy2(col.path, col.path + ".bak")
            os.replace(converted, col.path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------- media sync


class MediaDB:
    """Tracks which media files have been synced (like Anki's collection.media.db2)."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS media (
                fname TEXT NOT NULL PRIMARY KEY, csum TEXT, mtime INTEGER NOT NULL, dirty INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (dirMod INTEGER, lastUsn INTEGER);
            """
        )
        if self.db.execute("SELECT count() FROM meta").fetchone()[0] == 0:
            self.db.execute("INSERT INTO meta VALUES (0, 0)")
        self.db.commit()

    @property
    def last_usn(self) -> int:
        return self.db.execute("SELECT lastUsn FROM meta").fetchone()[0]

    def set_last_usn(self, usn: int) -> None:
        self.db.execute("UPDATE meta SET lastUsn = ?", (usn,))

    def entry(self, fname: str):
        return self.db.execute("SELECT csum, mtime, dirty FROM media WHERE fname = ?", (fname,)).fetchone()

    def count(self) -> int:
        return self.db.execute("SELECT count() FROM media WHERE csum IS NOT NULL").fetchone()[0]

    def pending(self, limit: int) -> list[tuple[str, str | None]]:
        return self.db.execute("SELECT fname, csum FROM media WHERE dirty = 1 LIMIT ?", (limit,)).fetchall()

    def force_resync(self) -> None:
        self.db.execute("DELETE FROM media")
        self.db.execute("UPDATE meta SET lastUsn = 0, dirMod = 0")
        self.db.commit()

    def close(self) -> None:
        self.db.close()


def _sha1(path: str) -> str:
    import hashlib

    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _media_file_ok(name: str) -> bool:
    return bool(name) and not name.startswith(".") and name not in ("thumbs.db", "desktop.ini")


class MediaSyncer:
    MAX_FILES_IN_ZIP = 25
    TARGET_ZIP_BYTES = int(2.5 * 1024 * 1024)
    MAX_FILE_SIZE = 100 * 1024 * 1024

    def __init__(self, media_dir: str, db_path: str, client: HttpSyncClient, progress: Progress | None = None):
        self.media_dir = media_dir
        self.db = MediaDB(db_path)
        self.client = client
        self.progress = progress or (lambda _m: None)
        self.stats = {"downloaded": 0, "uploaded": 0, "deleted": 0}

    def register_changes(self) -> None:
        import unicodedata

        db = self.db.db
        seen = set()
        for name in os.listdir(self.media_dir):
            path = os.path.join(self.media_dir, name)
            if not os.path.isfile(path) or not _media_file_ok(name):
                continue
            if unicodedata.normalize("NFC", name) != name:
                continue
            seen.add(name)
            mtime = int(os.path.getmtime(path))
            row = self.db.entry(name)
            if row is not None and row[1] == mtime and row[0] is not None:
                continue
            csum = _sha1(path)
            if row is None or row[0] != csum:
                db.execute("INSERT OR REPLACE INTO media VALUES (?,?,?,1)", (name, csum, mtime))
            else:
                db.execute("UPDATE media SET mtime = ? WHERE fname = ?", (mtime, name))
        for (name,) in db.execute("SELECT fname FROM media WHERE csum IS NOT NULL").fetchall():
            if name not in seen:
                db.execute("UPDATE media SET csum = NULL, mtime = 0, dirty = 1 WHERE fname = ?", (name,))
        db.commit()

    def sync(self, server_usn: int | None = None) -> dict:
        self.progress("Checking media...")
        self.register_changes()
        if server_usn is None:
            begin = self.client.media_json("msync/begin", {"v": CLIENT_VERSION})
            server_usn = int(begin["usn"])
        acted = False
        if self.db.last_usn != server_usn:
            self._fetch_changes()
            acted = True
        if self.db.pending(1):
            self._send_changes()
            acted = True
        if acted:
            self._finalize()
        self.db.close()
        return self.stats

    def _fetch_changes(self) -> None:
        db = self.db.db
        last_usn = self.db.last_usn
        while True:
            batch = self.client.media_json("msync/mediaChanges", {"lastUsn": last_usn})
            if not batch:
                break
            last_usn = batch[-1][1]
            to_download, to_delete, to_clean = [], [], []
            for fname, _usn, remote_sha in batch:
                row = self.db.entry(fname)
                local_sha = (row[0] or "") if row else ""
                pending = bool(row and row[2])
                if not local_sha and not remote_sha:
                    if row is not None:
                        to_delete.append(fname)
                elif not local_sha:
                    to_download.append(fname)
                elif not remote_sha:
                    if not pending:
                        to_delete.append(fname)
                elif local_sha == remote_sha:
                    if pending:
                        to_clean.append(fname)
                else:
                    to_download.append(fname)
            for fname in to_delete:
                path = os.path.join(self.media_dir, fname)
                if os.path.isfile(path):
                    os.remove(path)
                db.execute("DELETE FROM media WHERE fname = ?", (fname,))
                self.stats["deleted"] += 1
            for i in range(0, len(to_download), self.MAX_FILES_IN_ZIP):
                names = to_download[i : i + self.MAX_FILES_IN_ZIP]
                self.progress(f"Downloading media ({self.stats['downloaded'] + len(names)} files)...")
                data = self.client.request("msync/downloadFiles", json.dumps({"files": names}).encode())
                self._extract(data)
            for fname in to_clean:
                db.execute("UPDATE media SET dirty = 0 WHERE fname = ?", (fname,))
            self.db.set_last_usn(last_usn)
            db.commit()

    def _extract(self, data: bytes) -> None:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = json.loads(zf.read("_meta"))
            for info in zf.infolist():
                if info.filename == "_meta":
                    continue
                real = names.get(info.filename)
                if not real or os.sep in real or real.startswith("."):
                    continue
                content = zf.read(info)
                path = os.path.join(self.media_dir, real)
                with open(path, "wb") as f:
                    f.write(content)
                import hashlib

                self.db.db.execute(
                    "INSERT OR REPLACE INTO media VALUES (?,?,?,0)",
                    (real, hashlib.sha1(content).hexdigest(), int(os.path.getmtime(path))),
                )
                self.stats["downloaded"] += 1

    def _send_changes(self) -> None:
        import zipfile

        db = self.db.db
        while True:
            pending = self.db.pending(self.MAX_FILES_IN_ZIP)
            if not pending:
                break
            entries = []
            buf = io.BytesIO()
            size = 0
            invalid = []
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
                for idx, (fname, csum) in enumerate(pending):
                    if size > self.TARGET_ZIP_BYTES:
                        break
                    if csum is None:
                        entries.append([fname, None])
                        continue
                    path = os.path.join(self.media_dir, fname)
                    try:
                        with open(path, "rb") as f:
                            content = f.read()
                    except OSError:
                        invalid.append(fname)
                        continue
                    if not content or len(content) > self.MAX_FILE_SIZE:
                        invalid.append(fname)
                        continue
                    zf.writestr(str(idx), content)
                    entries.append([fname, str(idx)])
                    size += len(content)
                zf.writestr("_meta", json.dumps(entries))
            if invalid:
                for fname in invalid:
                    db.execute("DELETE FROM media WHERE fname = ?", (fname,))
                db.commit()
                continue
            self.progress(f"Uploading media ({self.stats['uploaded'] + len(entries)} files)...")
            raw = self.client.request("msync/uploadChanges", buf.getvalue())
            resp = json.loads(raw)
            if resp.get("err"):
                raise SyncError(resp["err"], "server")
            processed, current_usn = resp["data"]
            done = entries[:processed]
            for fname, _ in done:
                row = self.db.entry(fname)
                if row and row[0] is None:
                    db.execute("DELETE FROM media WHERE fname = ?", (fname,))
                else:
                    db.execute("UPDATE media SET dirty = 0 WHERE fname = ?", (fname,))
            if self.db.last_usn + len(done) == current_usn:
                self.db.set_last_usn(current_usn)
            db.commit()
            self.stats["uploaded"] += len(done)
            if processed == 0:
                break

    def _finalize(self) -> None:
        result = self.client.media_json("msync/mediaSanity", {"local": self.db.count()})
        if result != "OK":
            self.db.force_resync()
            raise SyncError("Media sync found an inconsistency; it will be repeated from scratch next time.", "resync")


def media_db_path(col: "Collection") -> str:
    base = col.path[:-6] if col.path.endswith(".anki2") else col.path
    return base + ".media.ankitui.db"
