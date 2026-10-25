"""Exporting decks (.apkg), whole collections (.colpkg) and notes as text."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from typing import TYPE_CHECKING

from . import consts
from .collection import SCHEMA_SQL, default_deck_conf
from .importing import media_references
from .models import CARD_COLUMNS, NOTE_COLUMNS, Card
from .utils import int_time, strip_html

if TYPE_CHECKING:
    from .collection import Collection


def _write_media(zf: zipfile.ZipFile, media_dir: str, names: set[str]) -> int:
    mapping: dict[str, str] = {}
    idx = 0
    for name in sorted(names):
        path = os.path.join(media_dir, name)
        if not os.path.isfile(path):
            continue
        zf.write(path, str(idx))
        mapping[str(idx)] = name
        idx += 1
    zf.writestr("media", json.dumps(mapping))
    return idx


def export_apkg(
    col: "Collection",
    out_path: str,
    did: int | None = None,
    include_scheduling: bool = True,
    include_media: bool = True,
) -> tuple[int, int]:
    """Export a deck (and its subdecks), or everything when did is None.

    Produces a legacy (schema 11) package that every Anki client can import.
    Returns (notes exported, cards exported).
    """
    col.save()
    if did is None:
        dids = [int(d["id"]) for d in col.decks.values()]
    else:
        dids = col.child_ids(did, include_self=True)
    q = ",".join(str(d) for d in dids)
    rows = col.db.execute(
        f"SELECT {CARD_COLUMNS} FROM cards WHERE did IN ({q}) OR odid IN ({q})"
    ).fetchall()
    cards = [Card.from_row(r) for r in rows]
    nids = sorted({c.nid for c in cards})

    tmpdir = tempfile.mkdtemp(prefix="ankitui-export-")
    tmp_col = os.path.join(tmpdir, "collection.anki21")
    db = sqlite3.connect(tmp_col)
    db.executescript(SCHEMA_SQL)

    used_dids: set[int] = set()
    for card in cards:
        if card.odid:
            # filtered decks are not exported: cards go back to their home deck
            card.did = card.odid
            card.odid = 0
            if card.odue:
                card.due = card.odue
            card.odue = 0
            if card.queue == consts.QUEUE_PREVIEW or (card.queue == consts.QUEUE_REV and card.type != consts.CARD_TYPE_REV):
                from .sched.scheduler import _restore_queue_from_type

                _restore_queue_from_type(card)
        if not include_scheduling:
            card.type = consts.CARD_TYPE_NEW
            card.queue = consts.QUEUE_NEW
            card.ivl = 0
            card.factor = 0
            card.reps = 0
            card.lapses = 0
            card.left = 0
            card.flags = 0
            card.data = ""
        used_dids.add(card.did)
    if not include_scheduling:
        # new cards keep the relative order of their notes
        positions = {nid: i + 1 for i, nid in enumerate(nids)}
        for card in cards:
            card.due = positions[card.nid]
    db.executemany(f"INSERT INTO cards VALUES ({','.join('?' * 18)})", [c.as_row() for c in cards])

    notes = []
    media: set[str] = set()
    mids: set[int] = set()
    for chunk_start in range(0, len(nids), 500):
        chunk = nids[chunk_start : chunk_start + 500]
        cq = ",".join(str(n) for n in chunk)
        for row in col.db.execute(f"SELECT {NOTE_COLUMNS} FROM notes WHERE id IN ({cq})"):
            row = list(row)
            if not include_scheduling:
                tags = [t for t in row[5].split() if t.lower() not in ("leech", "marked")]
                row[5] = (" " + " ".join(tags) + " ") if tags else ""
            notes.append(row)
            mids.add(row[2])
            if include_media:
                for fld in row[6].split("\x1f"):
                    media |= media_references(fld)
    db.executemany(f"INSERT INTO notes VALUES ({','.join('?' * 11)})", notes)

    if include_scheduling and cards:
        cq_all = [c.id for c in cards]
        for start in range(0, len(cq_all), 500):
            cq = ",".join(str(c) for c in cq_all[start : start + 500])
            db.executemany(
                "INSERT INTO revlog VALUES (?,?,?,?,?,?,?,?,?)",
                col.db.execute(f"SELECT * FROM revlog WHERE cid IN ({cq})").fetchall(),
            )

    # decks: exported decks plus their parents, so the hierarchy survives
    export_dids = set(dids if did is not None else used_dids) | used_dids
    for d in list(export_dids):
        export_dids.update(col.parent_ids(d))
    decks = {}
    conf_ids = {1}
    for d in export_dids:
        deck = col.get_deck(d)
        if deck is None or deck["dyn"]:
            continue
        deck = json.loads(json.dumps(deck))
        if not include_scheduling:
            deck["conf"] = 1
            for key in ("newToday", "revToday", "lrnToday", "timeToday"):
                deck[key] = [0, 0]
        conf_ids.add(int(deck.get("conf", 1)))
        decks[str(d)] = deck
    if "1" not in decks:
        default = json.loads(json.dumps(col.get_deck(1)))
        decks["1"] = default
    dconf = {}
    for cid in conf_ids:
        conf = col.dconf.get(str(cid))
        if conf is not None and (include_scheduling or cid == 1):
            dconf[str(cid)] = conf
    if "1" not in dconf or not include_scheduling:
        dconf["1"] = default_deck_conf()
    models = {str(m): col.models[str(m)] for m in mids if str(m) in col.models}
    if include_media:
        for nt in models.values():
            for tmpl in nt["tmpls"]:
                for m in re.finditer(r"[\"'](_[^\"']+)[\"']", tmpl["qfmt"] + tmpl["afmt"] + nt.get("css", "")):
                    media.add(m.group(1))
    conf = dict(col.conf)
    conf["curDeck"] = did or 1
    conf["activeDecks"] = [did or 1]
    tags = {}
    for row in notes:
        for t in row[5].split():
            tags[t] = 0
    db.execute(
        "INSERT INTO col VALUES (1,?,?,?,?,0,0,0,?,?,?,?,?)",
        (
            col.crt,
            int_time(1000),
            int_time(1000),
            consts.SCHEMA_VERSION,
            json.dumps(conf),
            json.dumps(models),
            json.dumps(decks),
            json.dumps(dconf),
            json.dumps(tags),
        ),
    )
    db.commit()
    db.close()

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp_col, "collection.anki21")
        _write_media(zf, col.media_dir, media if include_media else set())
    os.remove(tmp_col)
    os.rmdir(tmpdir)
    return len(notes), len(cards)


def export_colpkg(col: "Collection", out_path: str, include_media: bool = True) -> None:
    """Export the whole collection as a legacy .colpkg backup."""
    col.save()
    tmpdir = tempfile.mkdtemp(prefix="ankitui-export-")
    tmp_col = os.path.join(tmpdir, "collection.anki21")
    dst = sqlite3.connect(tmp_col)
    col.db.backup(dst)
    dst.close()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp_col, "collection.anki21")
        names = set(os.listdir(col.media_dir)) if include_media else set()
        _write_media(zf, col.media_dir, names)
    os.remove(tmp_col)
    os.rmdir(tmpdir)


def export_notes_text(
    col: "Collection",
    out_path: str,
    did: int | None = None,
    include_html: bool = False,
    include_tags: bool = True,
    include_headers: bool = True,
) -> int:
    """Export notes as tab-separated text. Returns the number of notes written."""
    if did is None:
        nids = [r[0] for r in col.db.execute("SELECT id FROM notes ORDER BY id")]
    else:
        q = ",".join(str(d) for d in col.child_ids(did, include_self=True))
        nids = [
            r[0]
            for r in col.db.execute(
                f"SELECT DISTINCT nid FROM cards WHERE did IN ({q}) OR odid IN ({q}) ORDER BY nid"
            )
        ]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        if include_headers:
            f.write("#separator:tab\n")
            f.write(f"#html:{'true' if include_html else 'false'}\n")
        writer = csv.writer(f, delimiter="\t", quotechar='"', lineterminator="\n")
        count = 0
        nfields_max = 0
        rows = []
        for nid in nids:
            note = col.get_note(nid)
            fields = [fld if include_html else strip_html(fld.replace("<br>", "\n")) for fld in note.fields]
            nfields_max = max(nfields_max, len(fields))
            rows.append((fields, note.tags))
        if include_headers and include_tags:
            f.write(f"#tags column:{nfields_max + 1}\n")
        for fields, tags in rows:
            fields = fields + [""] * (nfields_max - len(fields))
            if include_tags:
                fields.append(" ".join(tags))
            writer.writerow(fields)
            count += 1
    return count
