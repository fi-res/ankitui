"""Conversion of Anki's modern schema 18 collections into schema 11.

Modern packages (.apkg/.colpkg exported by Anki 2.1.50+, containing
collection.anki21b) store decks, notetypes and options as protobuf blobs in
separate tables. Here they are turned back into the schema 11 JSON layout.
"""

from __future__ import annotations

import json
import sqlite3

from . import consts
from .collection import SCHEMA_SQL, default_conf, default_deck_conf, default_filtered_deck, default_normal_deck
from .models import new_field, new_notetype, new_template
from .protobuf import Message


def _unicase(a: str, b: str) -> int:
    a, b = a.lower(), b.lower()
    return (a > b) - (a < b)


def _deck_from_row(did: int, name: str, mtime: int, usn: int, common_blob: bytes, kind_blob: bytes) -> dict:
    name = name.replace("\x1f", "::")
    common = Message(common_blob)
    kind = Message(kind_blob)
    filtered = kind.msg(2)
    if filtered is not None:
        deck = default_filtered_deck(did, name)
        deck["resched"] = filtered.bool(1)
        deck["terms"] = [[t.str(1), t.int(2), t.int(3)] for t in filtered.msgs(2)] or [["", 100, 0]]
        delays = filtered.floats(3)
        deck["delays"] = delays or None
        deck["previewDelay"] = filtered.int(4)
        deck["previewAgainSecs"] = filtered.int(7)
        deck["previewHardSecs"] = filtered.int(5)
        deck["previewGoodSecs"] = filtered.int(6)
    else:
        normal = kind.msg(1) or Message()
        deck = default_normal_deck(did, name, normal.sint(1) or 1)
        deck["extendNew"] = normal.int(2)
        deck["extendRev"] = normal.int(3)
        deck["desc"] = normal.str(4)
        if normal.has(6):
            deck["reviewLimit"] = normal.int(6)
        if normal.has(7):
            deck["newLimit"] = normal.int(7)
        for num, key in ((8, "reviewLimitToday"), (9, "newLimitToday")):
            day = normal.msg(num)
            if day is not None:
                deck[key] = {"limit": day.int(1), "today": day.int(2)}
    day = common.int(3)
    deck["collapsed"] = common.bool(1)
    deck["browserCollapsed"] = common.bool(2)
    deck["newToday"] = [day, common.sint(4)]
    deck["revToday"] = [day, common.sint(5)]
    deck["lrnToday"] = [day, common.sint(6)]
    deck["timeToday"] = [day, common.sint(7)]
    deck["mod"] = mtime
    deck["usn"] = usn
    return deck


def _deck_config_from_row(cid: int, name: str, mtime: int, usn: int, blob: bytes) -> dict:
    c = Message(blob)
    conf = default_deck_conf(cid, name)
    conf["mod"] = mtime
    conf["usn"] = usn
    conf["new"]["delays"] = c.floats(1)
    conf["lapse"]["delays"] = c.floats(2)
    conf["new"]["perDay"] = c.int(9)
    conf["rev"]["perDay"] = c.int(10)
    conf["new"]["initialFactor"] = int(round(c.float(11, 2.5) * 1000))
    conf["rev"]["ease4"] = c.float(12, 1.3)
    conf["rev"]["hardFactor"] = c.float(13, 1.2)
    conf["lapse"]["mult"] = c.float(14, 0.0)
    conf["rev"]["ivlFct"] = c.float(15, 1.0)
    conf["rev"]["maxIvl"] = c.int(16, 36500)
    conf["lapse"]["minInt"] = c.int(17, 1)
    conf["new"]["ints"] = [c.int(18, 1), c.int(19, 4), 0]
    conf["new"]["order"] = 0 if c.int(20) == consts.NEW_INSERT_RANDOM else 1
    conf["lapse"]["leechAction"] = c.int(21)
    conf["lapse"]["leechFails"] = c.int(22)
    conf["autoplay"] = not c.bool(23)
    conf["maxTaken"] = c.int(24, 60)
    conf["timer"] = 1 if c.bool(25) else 0
    conf["replayq"] = not c.bool(26)
    conf["new"]["bury"] = c.bool(27)
    conf["rev"]["bury"] = c.bool(28)
    conf["buryInterdayLearning"] = c.bool(29)
    conf["newMix"] = c.int(30)
    conf["interdayLearningMix"] = c.int(31)
    conf["newSortOrder"] = c.int(32)
    conf["reviewOrder"] = c.int(33)
    conf["newGatherPriority"] = c.int(34)
    conf["newPerDayMinimum"] = c.int(35)
    return conf


def _notetypes(db: sqlite3.Connection) -> dict[str, dict]:
    models: dict[str, dict] = {}
    for ntid, name, mtime, usn, blob in db.execute("SELECT id, name, mtime_secs, usn, config FROM notetypes"):
        cfg = Message(blob)
        nt = new_notetype(name, cfg.int(1))
        nt["id"] = ntid
        nt["mod"] = mtime
        nt["usn"] = usn
        nt["sortf"] = cfg.int(2)
        nt["css"] = cfg.str(3)
        nt["latexPre"] = cfg.str(5) or nt["latexPre"]
        nt["latexPost"] = cfg.str(6) or nt["latexPost"]
        nt["latexsvg"] = cfg.bool(7)
        nt["req"] = [[r.int(1), ["none", "any", "all"][min(r.int(2), 2)], [int(x) for x in _packed_ints(r, 3)]] for r in cfg.msgs(8)]
        for ord_, fname, fblob in db.execute("SELECT ord, name, config FROM fields WHERE ntid=? ORDER BY ord", (ntid,)):
            fc = Message(fblob)
            fld = new_field(fname, ord_)
            fld["sticky"] = fc.bool(1)
            fld["rtl"] = fc.bool(2)
            fld["font"] = fc.str(3) or "Arial"
            fld["size"] = fc.int(4) or 20
            fld["description"] = fc.str(5)
            fld["plainText"] = fc.bool(6)
            fld["collapsed"] = fc.bool(7)
            fld["excludeFromSearch"] = fc.bool(8)
            nt["flds"].append(fld)
        for ord_, tname, tblob in db.execute(
            "SELECT ord, name, config FROM templates WHERE ntid=? ORDER BY ord", (ntid,)
        ):
            tc = Message(tblob)
            tmpl = new_template(tname, ord_, tc.str(1), tc.str(2))
            tmpl["bqfmt"] = tc.str(3)
            tmpl["bafmt"] = tc.str(4)
            target = tc.sint(5)
            tmpl["did"] = target or None
            tmpl["bfont"] = tc.str(6)
            tmpl["bsize"] = tc.int(7)
            nt["tmpls"].append(tmpl)
        models[str(ntid)] = nt
    return models


def _packed_ints(msg: Message, num: int) -> list[int]:
    from .protobuf import read_varint

    out = []
    for wire, val in msg.fields.get(num, []):
        if wire == 0:
            out.append(val)
        elif wire == 2:
            pos = 0
            while pos < len(val):
                v, pos = read_varint(val, pos)
                out.append(v)
    return out


def convert_to_schema11(src_path: str, dst_path: str) -> None:
    """Write a schema 11 copy of the collection at src_path to dst_path."""
    src = sqlite3.connect(src_path)
    src.create_collation("unicase", _unicase)
    ver = src.execute("SELECT ver FROM col").fetchone()[0]
    dst = sqlite3.connect(dst_path)
    dst.executescript(SCHEMA_SQL)
    crt, mod, scm, usn, ls = src.execute("SELECT crt, mod, scm, usn, ls FROM col").fetchone()
    if ver < 15:
        row = src.execute("SELECT conf, models, decks, dconf, tags FROM col").fetchone()
        conf, models, decks, dconf, tags = row
    else:
        conf_dict = default_conf()
        conf_dict.pop("creationOffset", None)
        for key, val in src.execute("SELECT KEY, val FROM config"):
            try:
                conf_dict[key] = json.loads(val)
            except ValueError:
                pass
        decks_dict = {}
        for row in src.execute("SELECT id, name, mtime_secs, usn, common, kind FROM decks"):
            deck = _deck_from_row(*row)
            decks_dict[str(deck["id"])] = deck
        dconf_dict = {}
        for row in src.execute("SELECT id, name, mtime_secs, usn, config FROM deck_config"):
            c = _deck_config_from_row(*row)
            dconf_dict[str(c["id"])] = c
        if "1" not in dconf_dict:
            dconf_dict["1"] = default_deck_conf()
        tags_dict = {t: u for t, u in src.execute("SELECT tag, usn FROM tags")}
        conf, models, decks, dconf, tags = (
            json.dumps(conf_dict),
            json.dumps(_notetypes(src)),
            json.dumps(decks_dict),
            json.dumps(dconf_dict),
            json.dumps(tags_dict),
        )
    dst.execute(
        "INSERT INTO col VALUES (1,?,?,?,?,0,?,?,?,?,?,?,?)",
        (crt, mod, scm, consts.SCHEMA_VERSION, usn, ls, conf, models, decks, dconf, tags),
    )
    for table, ncols in (("notes", 11), ("cards", 18), ("revlog", 9)):
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        dst.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({','.join('?' * ncols)})", rows)
    dst.commit()
    dst.close()
    src.close()
