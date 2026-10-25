"""Importing .apkg/.colpkg packages and delimited text files."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import consts
from .collection import sched_timing_today, local_minutes_west
from .models import CARD_COLUMNS, NOTE_COLUMNS, Card, Note, field_names
from .protobuf import Message
from .schema18 import convert_to_schema11
from .sched.scheduler import _restore_queue_from_type
from .utils import guid64, int_time, parse_tags

if TYPE_CHECKING:
    from .collection import Collection


class ImportError_(Exception):
    pass


@dataclass
class ImportResult:
    notes_added: int = 0
    notes_updated: int = 0
    notes_skipped: int = 0
    cards_added: int = 0
    decks_added: list[str] = field(default_factory=list)
    notetypes_added: list[str] = field(default_factory=list)
    media_added: int = 0
    revlog_added: int = 0
    log: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Notes added: {self.notes_added}",
            f"Notes updated: {self.notes_updated}",
            f"Notes skipped (already present): {self.notes_skipped}",
            f"Cards added: {self.cards_added}",
        ]
        if self.decks_added:
            lines.append(f"Decks created: {', '.join(self.decks_added)}")
        if self.notetypes_added:
            lines.append(f"Note types added: {', '.join(self.notetypes_added)}")
        if self.media_added:
            lines.append(f"Media files added: {self.media_added}")
        lines.extend(self.log)
        return "\n".join(lines)


def _zstd_decompress(data: bytes) -> bytes:
    import zstandard

    return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()


class Package:
    """An extracted package: a schema 11 collection file plus media accessors."""

    def __init__(self, path: str):
        if not zipfile.is_zipfile(path):
            raise ImportError_("The file is not a valid Anki package (zip expected).")
        self.zip = zipfile.ZipFile(path)
        self.tmpdir = tempfile.mkdtemp(prefix="ankitui-import-")
        names = set(self.zip.namelist())
        self.latest = False
        if "meta" in names:
            meta = Message(self.zip.read("meta"))
            self.latest = meta.int(1) >= 3
        self.col_path = os.path.join(self.tmpdir, "collection.anki2")
        if "collection.anki21b" in names:
            raw = os.path.join(self.tmpdir, "raw.anki21b")
            with open(raw, "wb") as f:
                f.write(_zstd_decompress(self.zip.read("collection.anki21b")))
            convert_to_schema11(raw, self.col_path)
            self.latest = True
        elif "collection.anki21" in names or "collection.anki2" in names:
            name = "collection.anki21" if "collection.anki21" in names else "collection.anki2"
            raw = os.path.join(self.tmpdir, "raw.anki2")
            with open(raw, "wb") as f:
                f.write(self.zip.read(name))
            convert_to_schema11(raw, self.col_path)
        else:
            raise ImportError_("The package contains no collection.")
        self.media = self._read_media_map()

    def _read_media_map(self) -> dict[str, str]:
        """Return {filename: zip entry name}."""
        if "media" not in self.zip.namelist():
            return {}
        data = self.zip.read("media")
        if self.latest:
            entries = Message(_zstd_decompress(data)).msgs(1)
            return {e.str(1): str(i) for i, e in enumerate(entries)}
        try:
            mapping = json.loads(data.decode("utf-8") or "{}")
        except ValueError:
            return {}
        return {name: idx for idx, name in mapping.items()}

    def read_media(self, name: str) -> bytes:
        data = self.zip.read(self.media[name])
        if self.latest:
            data = _zstd_decompress(data)
        return data

    def close(self) -> None:
        self.zip.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class _Source:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        crt, conf, models, decks, dconf = self.db.execute(
            "SELECT crt, conf, models, decks, dconf FROM col"
        ).fetchone()
        self.crt = crt
        self.conf = json.loads(conf)
        self.models = json.loads(models)
        self.decks = json.loads(decks)
        self.dconf = json.loads(dconf)

    def today(self) -> int:
        import time

        now = int(time.time())
        return sched_timing_today(
            self.crt, now, self.conf.get("creationOffset"), local_minutes_west(now), self.conf.get("rollover", 4)
        ).days_elapsed


def _notetype_schema(nt: dict) -> tuple:
    return (
        nt.get("type", 0),
        tuple(f["name"] for f in sorted(nt["flds"], key=lambda f: f["ord"])),
        len(nt["tmpls"]),
    )


_MEDIA_RE = (
    re.compile(r"(?i)(<img[^>]*?src=[\"']?)([^\"'>\s]+)"),
    re.compile(r"(\[sound:)([^\]]+)(\])"),
)


def media_references(text: str) -> set[str]:
    refs = set()
    for m in _MEDIA_RE[0].finditer(text):
        refs.add(m.group(2))
    for m in _MEDIA_RE[1].finditer(text):
        refs.add(m.group(2))
    return {r for r in refs if "://" not in r}


def import_apkg(col: "Collection", path: str) -> ImportResult:
    """Merge a deck package into the collection, following Anki's merge rules."""
    pkg = Package(path)
    try:
        return _merge(col, pkg)
    finally:
        pkg.close()


def _merge(col: "Collection", pkg: Package) -> ImportResult:
    res = ImportResult()
    src = _Source(pkg.col_path)
    try:
        # --- note types
        mid_map: dict[int, int] = {}
        used_mids = {r[0] for r in src.db.execute("SELECT DISTINCT mid FROM notes")}
        for mid in used_mids:
            snt = src.models.get(str(mid))
            if snt is None:
                continue
            tnt = col.get_notetype(mid)
            if tnt is not None and _notetype_schema(tnt) == _notetype_schema(snt):
                mid_map[mid] = mid
                continue
            match = None
            for existing in col.models.values():
                if existing["name"] == snt["name"] and _notetype_schema(existing) == _notetype_schema(snt):
                    match = existing
                    break
            if match is not None:
                mid_map[mid] = int(match["id"])
                continue
            nt = dict(snt)
            if tnt is not None:
                nt["id"] = 0
            added = col.add_notetype(nt)
            mid_map[mid] = int(added["id"])
            res.notetypes_added.append(added["name"])

        # --- deck options and decks
        conf_map: dict[int, int] = {}
        did_map: dict[int, int] = {}

        def map_deck(sdid: int) -> int:
            if sdid in did_map:
                return did_map[sdid]
            sdeck = src.decks.get(str(sdid))
            if sdeck is None:
                did_map[sdid] = col.current_deck_id() if not col.get_deck(col.current_deck_id())["dyn"] else 1
                return did_map[sdid]
            name = sdeck["name"]
            existing = col.deck_id_by_name(name)
            if existing is not None and col.get_deck(existing)["dyn"]:
                name = name + " (imported)"
                existing = col.deck_id_by_name(name)
            if existing is None:
                sconf_id = int(sdeck.get("conf", 1)) if not sdeck["dyn"] else 1
                if sconf_id not in conf_map:
                    if str(sconf_id) in col.dconf:
                        conf_map[sconf_id] = sconf_id
                    elif str(sconf_id) in src.dconf:
                        sconf = src.dconf[str(sconf_id)]
                        new_conf = col.add_config(sconf["name"], sconf)
                        conf_map[sconf_id] = int(new_conf["id"])
                    else:
                        conf_map[sconf_id] = 1
                existing = col.add_normal_deck(name, conf_map[sconf_id])
                if not sdeck["dyn"]:
                    deck = col.get_deck(existing)
                    deck["desc"] = sdeck.get("desc", "")
                res.decks_added.append(name)
            did_map[sdid] = existing
            return existing

        # --- notes
        guid_map = {g: (nid, mid, mod) for nid, g, mid, mod in col.db.execute("SELECT id, guid, mid, mod FROM notes")}
        note_id_map: dict[int, int] = {}
        update_notes: set[int] = set()
        src_today = src.today()
        tgt_today = col.today
        day_delta = tgt_today - src_today
        new_pos_rows = src.db.execute("SELECT min(due) FROM cards WHERE type = 0").fetchone()
        min_src_pos = new_pos_rows[0] or 0
        next_pos = int(col.conf.get("nextPos", 1))
        max_pos_used = next_pos - 1
        media_renames: dict[str, str] = {}

        notes_rows = src.db.execute(f"SELECT {NOTE_COLUMNS} FROM notes").fetchall()
        for row in notes_rows:
            note = Note.from_row(row)
            if note.mid not in mid_map:
                continue
            target_mid = mid_map[note.mid]
            existing = guid_map.get(note.guid)
            if existing is not None:
                tnid, tmid, tmod = existing
                note_id_map[note.id] = tnid
                if tmid == target_mid and note.mod > tmod:
                    tnote = col.get_note(tnid)
                    tnote.fields = note.fields
                    tnote.tags = sorted(set(tnote.tags) | set(note.tags), key=str.lower)
                    tnote.mod = note.mod
                    tnote.usn = col.usn()
                    col._write_note(tnote)
                    update_notes.add(tnid)
                    res.notes_updated += 1
                else:
                    res.notes_skipped += 1
                continue
            new_id = note.id
            if col.db.execute("SELECT 1 FROM notes WHERE id=?", (new_id,)).fetchone():
                new_id = col._next_id("notes")
            note_id_map[note.id] = new_id
            note.id = new_id
            note.mid = target_mid
            note.usn = col.usn()
            col._write_note(note)
            guid_map[note.guid] = (note.id, note.mid, note.mod)
            update_notes.add(note.id)
            res.notes_added += 1

        # --- cards
        card_id_map: dict[int, int] = {}
        existing_ords = {}
        for nid, ord_ in col.db.execute("SELECT nid, ord FROM cards"):
            existing_ords.setdefault(nid, set()).add(ord_)
        for row in src.db.execute(f"SELECT {CARD_COLUMNS} FROM cards ORDER BY id").fetchall():
            card = Card.from_row(row)
            tnid = note_id_map.get(card.nid)
            if tnid is None:
                continue
            if card.ord in existing_ords.get(tnid, set()):
                continue
            existing_ords.setdefault(tnid, set()).add(card.ord)
            if card.odid:
                card.did = card.odid
                card.odid = 0
                if card.odue:
                    card.due = card.odue
                card.odue = 0
                if card.queue == consts.QUEUE_PREVIEW:
                    card.queue = consts.QUEUE_REV if card.type == consts.CARD_TYPE_REV else card.type
            card.did = map_deck(card.did)
            if card.type == consts.CARD_TYPE_NEW or card.queue == consts.QUEUE_NEW:
                card.due = next_pos + max(card.due - min_src_pos, 0)
                max_pos_used = max(max_pos_used, card.due)
            elif card.queue in (consts.QUEUE_REV, consts.QUEUE_DAY_LRN) or (
                card.queue < 0 and card.type == consts.CARD_TYPE_REV
            ):
                card.due = card.due + day_delta
            if card.queue in (consts.QUEUE_SCHED_BURIED, consts.QUEUE_USER_BURIED):
                _restore_queue_from_type(card)
            old_id = card.id
            if col.db.execute("SELECT 1 FROM cards WHERE id=?", (card.id,)).fetchone():
                card.id = col._next_id("cards")
            card_id_map[old_id] = card.id
            card.nid = tnid
            card.usn = col.usn()
            col.db.execute(f"INSERT INTO cards ({CARD_COLUMNS}) VALUES ({','.join('?' * 18)})", card.as_row())
            res.cards_added += 1
        col.conf["nextPos"] = max(next_pos, max_pos_used + 1)

        # --- review history
        for row in src.db.execute("SELECT * FROM revlog").fetchall():
            rid, cid = row[0], row[1]
            if cid not in card_id_map:
                continue
            cur = col.db.execute(
                "INSERT OR IGNORE INTO revlog VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, card_id_map[cid], col.usn()) + tuple(row[3:]),
            )
            res.revlog_added += cur.rowcount

        # --- media
        if pkg.media:
            needed: set[str] = set()
            for nid in update_notes:
                note = col.get_note(nid)
                for fld in note.fields:
                    needed |= media_references(fld)
            for nt in col.models.values():
                for tmpl in nt["tmpls"]:
                    for m in re.finditer(r"[\"'](_[^\"']+)[\"']", tmpl["qfmt"] + tmpl["afmt"] + nt.get("css", "")):
                        needed.add(m.group(1))
            media_dir = col.media_dir
            for name in sorted(needed):
                if name not in pkg.media:
                    continue
                data = pkg.read_media(name)
                target = os.path.join(media_dir, name)
                if os.path.exists(target):
                    with open(target, "rb") as f:
                        if f.read() == data:
                            continue
                    base, ext = os.path.splitext(name)
                    new_name = f"{base}-{hashlib.sha1(data).hexdigest()[:8]}{ext}"
                    media_renames[name] = new_name
                    target = os.path.join(media_dir, new_name)
                    if os.path.exists(target):
                        continue
                with open(target, "wb") as f:
                    f.write(data)
                res.media_added += 1
            if media_renames:
                for nid in update_notes:
                    note = col.get_note(nid)
                    changed = False
                    for i, fld in enumerate(note.fields):
                        for old, new in media_renames.items():
                            if old in fld:
                                fld = fld.replace(old, new)
                                changed = True
                        note.fields[i] = fld
                    if changed:
                        col._write_note(note)
    finally:
        src.db.close()
    col.save()
    col.sched.clear_queues()
    return res


def import_colpkg(path: str, target_col_path: str) -> None:
    """Replace the collection at target_col_path with the package contents."""
    pkg = Package(path)
    try:
        media_dir = (target_col_path[:-6] if target_col_path.endswith(".anki2") else target_col_path) + ".media"
        os.makedirs(media_dir, exist_ok=True)
        backup = target_col_path + ".bak"
        if os.path.exists(target_col_path):
            shutil.copy2(target_col_path, backup)
        shutil.copy2(pkg.col_path, target_col_path)
        for name in pkg.media:
            safe = os.path.basename(name)
            if not safe:
                continue
            with open(os.path.join(media_dir, safe), "wb") as f:
                f.write(pkg.read_media(name))
    finally:
        pkg.close()


# ---------------------------------------------------------------- text files


@dataclass
class TextImportOptions:
    notetype_id: int
    deck_id: int
    delimiter: str | None = None
    allow_html: bool = True
    tags_column: int | None = None
    extra_tags: list[str] = field(default_factory=list)
    duplicates: str = "skip"  # "skip", "update" or "keep"


def _detect_delimiter(lines: list[str]) -> str:
    sample = "\n".join(lines[:20])
    for delim in ("\t", ";", ",", "|", ":"):
        if delim in sample:
            return delim
    return "\t"


def import_text(col: "Collection", path: str, opts: TextImportOptions) -> ImportResult:
    """Import a CSV/TSV file, honouring Anki's '#key:value' header lines."""
    res = ImportResult()
    with open(path, encoding="utf-8-sig", newline="") as f:
        content = f.read()
    lines = content.splitlines()
    body: list[str] = []
    delimiter = opts.delimiter
    allow_html = opts.allow_html
    tags_column = opts.tags_column
    notetype_id = opts.notetype_id
    deck_id = opts.deck_id
    for line in lines:
        if line.startswith("#") and ":" in line and not body:
            key, _, value = line[1:].partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "separator":
                delimiter = {"tab": "\t", "comma": ",", "semicolon": ";", "space": " ", "pipe": "|", "colon": ":"}.get(
                    value.lower(), value[:1] or None
                )
            elif key == "html":
                allow_html = value.lower() == "true"
            elif key == "tags column":
                tags_column = int(value) if value.isdigit() else None
            elif key == "tags":
                opts.extra_tags = opts.extra_tags + parse_tags(value)
            elif key == "notetype":
                nt = col.notetype_by_name(value)
                if nt:
                    notetype_id = int(nt["id"])
            elif key == "deck":
                deck_id = col.add_normal_deck(value)
            continue
        if line.strip():
            body.append(line)
    if delimiter is None:
        delimiter = _detect_delimiter(body)
    nt = col.get_notetype(notetype_id)
    if nt is None:
        raise ImportError_("Unknown note type")
    nfields = len(nt["flds"])
    reader = csv.reader(body, delimiter=delimiter, quotechar='"')
    for row in reader:
        if not row:
            continue
        tags = list(opts.extra_tags)
        if tags_column and 0 < tags_column <= len(row):
            tags += parse_tags(row[tags_column - 1])
            row = row[: tags_column - 1] + row[tags_column:]
        fields = [(c if allow_html else _escape_html(c)) for c in row[:nfields]]
        fields += [""] * (nfields - len(fields))
        note = col.new_note(nt)
        note.fields = fields
        note.tags = tags
        state = col.note_is_duplicate_or_empty(note)
        if state == "empty":
            res.notes_skipped += 1
            continue
        if state == "duplicate" and opts.duplicates != "keep":
            if opts.duplicates == "update":
                from .utils import field_checksum

                row_ = col.db.execute(
                    "SELECT id FROM notes WHERE csum=? AND mid=?", (field_checksum(fields[0]), note.mid)
                ).fetchone()
                if row_:
                    existing = col.get_note(row_[0])
                    existing.fields = fields
                    existing.tags = sorted(set(existing.tags) | set(tags), key=str.lower)
                    col.update_note(existing)
                    res.notes_updated += 1
                    continue
            res.notes_skipped += 1
            continue
        try:
            res.cards_added += col.add_note(note, deck_id)
            res.notes_added += 1
        except ValueError as err:
            res.notes_skipped += 1
            res.log.append(f"Skipped '{fields[0][:30]}': {err}")
    col.save()
    return res


def _escape_html(text: str) -> str:
    import html

    return html.escape(text, quote=False).replace("\n", "<br>")
