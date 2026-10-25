"""An Anki collection stored in the schema 11 SQLite format.

Schema 11 is the format used inside legacy .apkg/.colpkg packages and is read
by every Anki client (desktop, AnkiDroid, AnkiMobile).
"""

from __future__ import annotations

import copy
import json
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import consts
from .models import CARD_COLUMNS, NOTE_COLUMNS, Card, Note, field_names, stock_notetypes
from .template import RenderedCard, cloze_numbers_in_fields, render_card, template_is_nonempty
from .utils import (
    canonify_tags,
    field_checksum,
    guid64,
    int_time,
    join_tags,
    strip_html_media,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS col (
  id integer PRIMARY KEY, crt integer NOT NULL, mod integer NOT NULL,
  scm integer NOT NULL, ver integer NOT NULL, dty integer NOT NULL,
  usn integer NOT NULL, ls integer NOT NULL, conf text NOT NULL,
  models text NOT NULL, decks text NOT NULL, dconf text NOT NULL, tags text NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  id integer PRIMARY KEY, guid text NOT NULL, mid integer NOT NULL,
  mod integer NOT NULL, usn integer NOT NULL, tags text NOT NULL,
  flds text NOT NULL, sfld integer NOT NULL, csum integer NOT NULL,
  flags integer NOT NULL, data text NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
  id integer PRIMARY KEY, nid integer NOT NULL, did integer NOT NULL,
  ord integer NOT NULL, mod integer NOT NULL, usn integer NOT NULL,
  type integer NOT NULL, queue integer NOT NULL, due integer NOT NULL,
  ivl integer NOT NULL, factor integer NOT NULL, reps integer NOT NULL,
  lapses integer NOT NULL, left integer NOT NULL, odue integer NOT NULL,
  odid integer NOT NULL, flags integer NOT NULL, data text NOT NULL
);
CREATE TABLE IF NOT EXISTS revlog (
  id integer PRIMARY KEY, cid integer NOT NULL, usn integer NOT NULL,
  ease integer NOT NULL, ivl integer NOT NULL, lastIvl integer NOT NULL,
  factor integer NOT NULL, time integer NOT NULL, type integer NOT NULL
);
CREATE TABLE IF NOT EXISTS graves (
  usn integer NOT NULL, oid integer NOT NULL, type integer NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notes_usn ON notes (usn);
CREATE INDEX IF NOT EXISTS ix_cards_usn ON cards (usn);
CREATE INDEX IF NOT EXISTS ix_revlog_usn ON revlog (usn);
CREATE INDEX IF NOT EXISTS ix_cards_nid ON cards (nid);
CREATE INDEX IF NOT EXISTS ix_cards_sched ON cards (did, queue, due);
CREATE INDEX IF NOT EXISTS ix_revlog_cid ON revlog (cid);
CREATE INDEX IF NOT EXISTS ix_notes_csum ON notes (csum);
"""

GRAVE_CARD = 0
GRAVE_NOTE = 1
GRAVE_DECK = 2


def default_conf() -> dict:
    return {
        "activeDecks": [1],
        "curDeck": 1,
        "newSpread": 0,
        "collapseTime": 1200,
        "timeLim": 0,
        "estTimes": True,
        "dueCounts": True,
        "curModel": None,
        "nextPos": 1,
        "sortType": "noteFld",
        "sortBackwards": False,
        "addToCur": True,
        "dayLearnFirst": False,
        "schedVer": 2,
        "sched2021": True,
        "rollover": 4,
        "creationOffset": local_minutes_west(),
        "newCardsIgnoreReviewLimit": False,
        "applyAllParentLimits": False,
    }


def default_deck_conf(conf_id: int = 1, name: str = "Default") -> dict:
    return {
        "id": conf_id,
        "mod": 0,
        "name": name,
        "usn": 0,
        "maxTaken": 60,
        "autoplay": True,
        "timer": 0,
        "replayq": True,
        "dyn": False,
        "new": {
            "bury": False,
            "delays": [1.0, 10.0],
            "initialFactor": 2500,
            "ints": [1, 4, 0],
            "order": 1,
            "perDay": 20,
        },
        "rev": {
            "bury": False,
            "ease4": 1.3,
            "ivlFct": 1.0,
            "maxIvl": 36500,
            "perDay": 200,
            "hardFactor": 1.2,
        },
        "lapse": {
            "delays": [10.0],
            "leechAction": consts.LEECH_TAG_ONLY,
            "leechFails": 8,
            "minInt": 1,
            "mult": 0.0,
        },
        "newMix": consts.MIX_WITH_REVIEWS,
        "newPerDayMinimum": 0,
        "interdayLearningMix": consts.MIX_WITH_REVIEWS,
        "reviewOrder": consts.REVIEW_ORDER_DAY,
        "newSortOrder": consts.NEW_SORT_TEMPLATE,
        "newGatherPriority": consts.NEW_GATHER_DECK,
        "buryInterdayLearning": False,
    }


def _deck_common(did: int, name: str) -> dict:
    return {
        "id": did,
        "mod": int_time(),
        "name": name,
        "usn": -1,
        "lrnToday": [0, 0],
        "revToday": [0, 0],
        "newToday": [0, 0],
        "timeToday": [0, 0],
        "collapsed": False,
        "browserCollapsed": False,
        "desc": "",
    }


def default_normal_deck(did: int, name: str, conf_id: int = 1) -> dict:
    deck = _deck_common(did, name)
    deck.update(
        {
            "dyn": 0,
            "conf": conf_id,
            "extendNew": 0,
            "extendRev": 0,
            "reviewLimit": None,
            "newLimit": None,
            "reviewLimitToday": None,
            "newLimitToday": None,
        }
    )
    return deck


def default_filtered_deck(did: int, name: str) -> dict:
    deck = _deck_common(did, name)
    deck.update(
        {
            "dyn": 1,
            "resched": True,
            "terms": [["", 100, consts.DYN_RANDOM]],
            "separate": True,
            "delays": None,
            "previewDelay": 10,
            "previewAgainSecs": 60,
            "previewHardSecs": 600,
            "previewGoodSecs": 0,
        }
    )
    return deck


def local_minutes_west(stamp: float | None = None) -> int:
    dt = datetime.fromtimestamp(stamp if stamp is not None else time.time()).astimezone()
    offset = dt.utcoffset() or timedelta(0)
    return -int(offset.total_seconds() // 60)


def v1_creation_date(now: float | None = None) -> int:
    now = now if now is not None else time.time()
    tz = timezone(-timedelta(minutes=local_minutes_west(now)))
    now_dt = datetime.fromtimestamp(now, tz)
    four_am = now_dt.replace(hour=4, minute=0, second=0, microsecond=0)
    stamp = int(four_am.timestamp())
    return stamp - 86_400 if four_am > now_dt else stamp


@dataclass(frozen=True)
class SchedTiming:
    now: int
    days_elapsed: int
    next_day_at: int


def _tz(minutes_west: int) -> timezone:
    minutes_west = max(min(minutes_west, 23 * 60), -23 * 60)
    return timezone(-timedelta(minutes=minutes_west))


def _rollover_dt(dt: datetime, hour: int) -> datetime:
    return dt.replace(hour=hour % 24, minute=0, second=0, microsecond=0)


def sched_timing_today(
    crt: int,
    now: int,
    creation_offset: int | None,
    current_offset: int,
    rollover: int | None,
) -> SchedTiming:
    """Port of rslib/src/scheduler/timing.rs."""
    if rollover is None:
        days = (now - crt) // 86_400
        return SchedTiming(now, days, crt + (days + 1) * 86_400)
    cur_tz = _tz(current_offset)
    if creation_offset is None:
        crt_at_rollover = int(_rollover_dt(datetime.fromtimestamp(crt, cur_tz), rollover).timestamp())
        days = (now - crt_at_rollover) // 86_400
        next_day = int(_rollover_dt(datetime.fromtimestamp(now, cur_tz), rollover).timestamp())
        if next_day < now:
            next_day += 86_400
        return SchedTiming(now, max(days, 0), next_day)
    created = datetime.fromtimestamp(crt, _tz(creation_offset))
    now_dt = datetime.fromtimestamp(now, cur_tz)
    rollover_today = _rollover_dt(now_dt, rollover)
    passed = rollover_today <= now_dt
    next_day = rollover_today + timedelta(days=1) if passed else rollover_today
    days = now_dt.toordinal() - created.toordinal()
    if not passed:
        days -= 1
    return SchedTiming(now, max(days, 0), int(next_day.timestamp()))


class Collection:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        is_new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        # sync runs in a worker thread while the UI waits, so allow cross-thread use
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
        if is_new:
            self._create()
        self._load()
        from .sched.scheduler import Scheduler

        self.sched = Scheduler(self)

    # ------------------------------------------------------------------ setup

    @property
    def media_dir(self) -> str:
        base = self.path[:-6] if self.path.endswith(".anki2") else self.path
        folder = base + ".media"
        os.makedirs(folder, exist_ok=True)
        return folder

    def _create(self) -> None:
        self.db.executescript(SCHEMA_SQL)
        now = int_time()
        conf = default_conf()
        deck = default_normal_deck(1, "Default")
        deck["mod"] = 0
        dconf = default_deck_conf()
        self.db.execute(
            "INSERT OR REPLACE INTO col VALUES (1,?,?,?,?,0,0,0,?,?,?,?,?)",
            (
                v1_creation_date(),
                now * 1000,
                now * 1000,
                consts.SCHEMA_VERSION,
                json.dumps(conf),
                "{}",
                json.dumps({"1": deck}),
                json.dumps({"1": dconf}),
                "{}",
            ),
        )
        self.db.commit()
        self._load()
        first_id = None
        for nt in stock_notetypes():
            self.add_notetype(nt)
            first_id = first_id or nt["id"]
        self.conf["curModel"] = first_id
        self.save()

    def _load(self) -> None:
        row = self.db.execute(
            "SELECT crt, mod, scm, ver, usn, ls, conf, models, decks, dconf, tags FROM col"
        ).fetchone()
        (self.crt, self.mod, self.scm, self.ver, self.usn_, self.ls, conf, models, decks, dconf, tags) = row
        if self.ver != consts.SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported collection schema version {self.ver}; "
                "import it as a package (.colpkg/.apkg) instead."
            )
        self.conf: dict = json.loads(conf)
        self.models: dict[str, dict] = json.loads(models)
        self.decks: dict[str, dict] = json.loads(decks)
        self.dconf: dict[str, dict] = json.loads(dconf)
        self.tags: dict[str, int] = json.loads(tags)
        for key, value in default_conf().items():
            if key not in self.conf and key != "creationOffset":
                self.conf[key] = value

    def save(self, bump_mod: bool = True) -> None:
        if bump_mod:
            self.mod = int_time(1000)
        self.db.execute(
            "UPDATE col SET crt=?, mod=?, scm=?, usn=?, ls=?, conf=?, models=?, decks=?, dconf=?, tags=?",
            (
                self.crt,
                self.mod,
                self.scm,
                self.usn_,
                self.ls,
                json.dumps(self.conf),
                json.dumps(self.models),
                json.dumps(self.decks),
                json.dumps(self.dconf),
                json.dumps(self.tags),
            ),
        )
        self.db.commit()

    def close(self) -> None:
        if self.db:
            self.save(bump_mod=False)
            self.db.close()
            self.db = None  # type: ignore[assignment]

    def usn(self) -> int:
        return -1

    def mark_schema_modified(self) -> None:
        """The next sync will need to be a full sync (like Anki's schema change)."""
        self.scm = int_time(1000)

    # ----------------------------------------------------------------- timing

    def timing_today(self, now: int | None = None) -> SchedTiming:
        now = int(now if now is not None else time.time())
        return sched_timing_today(
            self.crt,
            now,
            self.conf.get("creationOffset"),
            local_minutes_west(now),
            self.conf.get("rollover", 4),
        )

    @property
    def today(self) -> int:
        return self.timing_today().days_elapsed

    # -------------------------------------------------------------------- ids

    def _next_id(self, table: str, base: int | None = None) -> int:
        nid = base if base is not None else int_time(1000)
        row = self.db.execute(f"SELECT max(id) FROM {table}").fetchone()
        if row and row[0] is not None and row[0] >= nid:
            nid = row[0] + 1
        return nid

    def _next_json_id(self, mapping: dict) -> int:
        nid = int_time(1000)
        while str(nid) in mapping:
            nid += 1
        return nid

    # ------------------------------------------------------------------ decks

    def get_deck(self, did: int) -> dict | None:
        return self.decks.get(str(did))

    def all_decks(self) -> list[dict]:
        return sorted(self.decks.values(), key=lambda d: [p.lower() for p in d["name"].split("::")])

    def all_deck_names(self, include_filtered: bool = True) -> list[str]:
        return [d["name"] for d in self.all_decks() if include_filtered or not d["dyn"]]

    def deck_id_by_name(self, name: str) -> int | None:
        lname = name.strip().lower()
        for deck in self.decks.values():
            if deck["name"].lower() == lname:
                return int(deck["id"])
        return None

    @staticmethod
    def normalize_deck_name(name: str) -> str:
        parts = [p.strip() for p in name.replace('"', "").split("::")]
        parts = [p for p in parts if p]
        return "::".join(parts)

    def add_normal_deck(self, name: str, conf_id: int = 1) -> int:
        name = self.normalize_deck_name(name)
        if not name:
            raise ValueError("Deck name must not be empty")
        existing = self.deck_id_by_name(name)
        if existing is not None:
            return existing
        self._ensure_parents(name)
        did = self._next_json_id(self.decks)
        self.decks[str(did)] = default_normal_deck(did, name, conf_id)
        self.save()
        return did

    def _ensure_parents(self, name: str) -> None:
        parts = name.split("::")
        for i in range(1, len(parts)):
            parent = "::".join(parts[:i])
            pid = self.deck_id_by_name(parent)
            if pid is None:
                pid = self._next_json_id(self.decks)
                self.decks[str(pid)] = default_normal_deck(pid, parent)
            elif self.decks[str(pid)]["dyn"]:
                raise ValueError("A filtered deck cannot have subdecks")

    def add_filtered_deck(self, name: str) -> int:
        name = self.normalize_deck_name(name)
        if self.deck_id_by_name(name) is not None:
            raise ValueError(f"A deck named '{name}' already exists")
        self._ensure_parents(name)
        did = self._next_json_id(self.decks)
        self.decks[str(did)] = default_filtered_deck(did, name)
        self.save()
        return did

    def save_deck(self, deck: dict) -> None:
        deck["mod"] = int_time()
        deck["usn"] = self.usn()
        self.decks[str(deck["id"])] = deck
        self.save()

    def rename_deck(self, did: int, new_name: str) -> None:
        new_name = self.normalize_deck_name(new_name)
        if not new_name:
            raise ValueError("Deck name must not be empty")
        deck = self.decks[str(did)]
        other = self.deck_id_by_name(new_name)
        if other is not None and other != did:
            raise ValueError(f"A deck named '{new_name}' already exists")
        old_name = deck["name"]
        if new_name.lower().startswith(old_name.lower() + "::"):
            raise ValueError("A deck cannot be moved into one of its own subdecks")
        self._ensure_parents(new_name)
        for d in self.decks.values():
            if d["name"] == old_name or d["name"].startswith(old_name + "::"):
                d["name"] = new_name + d["name"][len(old_name) :]
                d["mod"] = int_time()
                d["usn"] = self.usn()
        self.save()

    def child_ids(self, did: int, include_self: bool = False) -> list[int]:
        deck = self.decks.get(str(did))
        if not deck:
            return []
        prefix = deck["name"] + "::"
        ids = [int(d["id"]) for d in self.all_decks() if d["name"].startswith(prefix)]
        return ([did] if include_self else []) + ids

    def parent_ids(self, did: int) -> list[int]:
        deck = self.decks.get(str(did))
        if not deck:
            return []
        parts = deck["name"].split("::")
        ids = []
        for i in range(1, len(parts)):
            pid = self.deck_id_by_name("::".join(parts[:i]))
            if pid is not None:
                ids.append(pid)
        return ids

    def remove_deck(self, did: int) -> int:
        """Remove a deck and its children. Returns the number of cards deleted."""
        if did == consts.DEFAULT_DECK_ID and not self.child_ids(did):
            # Anki keeps the default deck; it just gets hidden when empty
            pass
        removed_cards = 0
        for d in reversed(self.child_ids(did, include_self=True)):
            deck = self.decks.get(str(d))
            if deck is None:
                continue
            if deck["dyn"]:
                self.sched.empty_filtered_deck(d)
            else:
                cids = [r[0] for r in self.db.execute("SELECT id FROM cards WHERE did=? OR odid=?", (d, d))]
                removed_cards += len(cids)
                self.remove_cards_and_orphaned_notes(cids)
            if d == consts.DEFAULT_DECK_ID:
                self.decks["1"]["name"] = "Default"
                continue
            del self.decks[str(d)]
            self.db.execute("INSERT INTO graves VALUES (?,?,?)", (self.usn(), d, GRAVE_DECK))
        if str(self.conf.get("curDeck")) not in self.decks:
            self.conf["curDeck"] = 1
        self.save()
        self.sched.clear_queues()
        return removed_cards

    def current_deck_id(self) -> int:
        did = int(self.conf.get("curDeck", 1))
        if str(did) not in self.decks:
            did = 1
        return did

    def set_current_deck(self, did: int) -> None:
        self.conf["curDeck"] = did
        self.conf["activeDecks"] = self.child_ids(did, include_self=True)
        self.save()
        self.sched.clear_queues()

    def deck_has_cards(self, did: int) -> bool:
        ids = self.child_ids(did, include_self=True)
        q = ",".join(str(i) for i in ids)
        return self.db.execute(f"SELECT 1 FROM cards WHERE did IN ({q}) LIMIT 1").fetchone() is not None

    # ----------------------------------------------------------- deck configs

    def config_for_deck(self, did: int) -> dict:
        deck = self.decks.get(str(did))
        if deck is None or deck["dyn"]:
            return self.dconf.get("1") or default_deck_conf()
        conf = self.dconf.get(str(deck.get("conf", 1)))
        if conf is None:
            conf = self.dconf.get("1") or default_deck_conf()
        return conf

    def all_configs(self) -> list[dict]:
        return sorted(self.dconf.values(), key=lambda c: (c["id"] != 1, c["name"].lower()))

    def add_config(self, name: str, clone_from: dict | None = None) -> dict:
        conf = copy.deepcopy(clone_from) if clone_from else default_deck_conf()
        cid = self._next_json_id(self.dconf)
        conf["id"] = cid
        conf["name"] = name
        conf["mod"] = int_time()
        conf["usn"] = self.usn()
        self.dconf[str(cid)] = conf
        self.save()
        return conf

    def save_config(self, conf: dict) -> None:
        conf["mod"] = int_time()
        conf["usn"] = self.usn()
        self.dconf[str(conf["id"])] = conf
        self.save()
        self.sched.clear_queues()

    def remove_config(self, conf_id: int) -> None:
        if conf_id == consts.DEFAULT_CONF_ID:
            raise ValueError("The default options preset cannot be removed")
        self.dconf.pop(str(conf_id), None)
        for deck in self.decks.values():
            if not deck["dyn"] and deck.get("conf") == conf_id:
                deck["conf"] = 1
        self.save()
        self.sched.clear_queues()

    def decks_using_config(self, conf_id: int) -> list[dict]:
        return [d for d in self.decks.values() if not d["dyn"] and d.get("conf") == conf_id]

    # -------------------------------------------------------------- notetypes

    def all_notetypes(self) -> list[dict]:
        return sorted(self.models.values(), key=lambda m: m["name"].lower())

    def get_notetype(self, mid: int) -> dict | None:
        return self.models.get(str(mid))

    def notetype_by_name(self, name: str) -> dict | None:
        for m in self.models.values():
            if m["name"].lower() == name.lower():
                return m
        return None

    def add_notetype(self, nt: dict) -> dict:
        nt = copy.deepcopy(nt)
        if not nt.get("id") or str(nt["id"]) in self.models:
            nt["id"] = self._next_json_id(self.models)
        base = nt["name"]
        n = 1
        while self.notetype_by_name(nt["name"]) is not None:
            n += 1
            nt["name"] = f"{base}-{n}"
        nt["mod"] = int_time()
        nt["usn"] = self.usn()
        for i, f in enumerate(nt["flds"]):
            f["ord"] = i
        for i, t in enumerate(nt["tmpls"]):
            t["ord"] = i
        self.models[str(nt["id"])] = nt
        self.save()
        return nt

    def save_notetype(self, nt: dict) -> None:
        nt["mod"] = int_time()
        nt["usn"] = self.usn()
        self.models[str(nt["id"])] = nt
        self.save()

    def current_notetype(self) -> dict:
        nt = self.models.get(str(self.conf.get("curModel")))
        if nt is None:
            nt = self.all_notetypes()[0]
        return nt

    def notetype_use_count(self, mid: int) -> int:
        return self.db.execute("SELECT count() FROM notes WHERE mid=?", (mid,)).fetchone()[0]

    def remove_notetype(self, mid: int) -> None:
        nids = [r[0] for r in self.db.execute("SELECT id FROM notes WHERE mid=?", (mid,))]
        self.remove_notes(nids)
        self.models.pop(str(mid), None)
        if str(self.conf.get("curModel")) == str(mid):
            self.conf["curModel"] = int(self.all_notetypes()[0]["id"]) if self.models else None
        self.mark_schema_modified()
        self.save()

    # ------------------------------------------------------------------ notes

    def new_note(self, notetype: dict) -> Note:
        return Note(
            id=0,
            guid=guid64(),
            mid=int(notetype["id"]),
            fields=[""] * len(notetype["flds"]),
            tags=[],
        )

    def get_note(self, nid: int) -> Note | None:
        row = self.db.execute(f"SELECT {NOTE_COLUMNS} FROM notes WHERE id=?", (nid,)).fetchone()
        return Note.from_row(row) if row else None

    def _note_sort_and_checksum(self, note: Note) -> tuple[str, int]:
        nt = self.models[str(note.mid)]
        sortf = min(nt.get("sortf", 0), len(note.fields) - 1)
        sfld = strip_html_media(note.fields[sortf]) if note.fields else ""
        csum = field_checksum(note.fields[0]) if note.fields else 0
        return sfld, csum

    def _write_note(self, note: Note) -> None:
        note.tags = canonify_tags(note.tags)
        self.register_tags(note.tags)
        sfld, csum = self._note_sort_and_checksum(note)
        self.db.execute(
            f"INSERT OR REPLACE INTO notes ({NOTE_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                note.id, note.guid, note.mid, note.mod, note.usn, join_tags(note.tags),
                note.flds, sfld, csum, note.flags, note.data,
            ),
        )

    def note_fields_map(self, note: Note) -> dict[str, str]:
        nt = self.models[str(note.mid)]
        names = field_names(nt)
        fields = list(note.fields) + [""] * (len(names) - len(note.fields))
        return dict(zip(names, fields))

    def card_ords_for_note(self, note: Note) -> list[int]:
        nt = self.models[str(note.mid)]
        if nt.get("type") == consts.MODEL_CLOZE:
            nums = cloze_numbers_in_fields(nt, note.fields)
            return sorted(n - 1 for n in nums if n > 0) or [0]
        fmap = self.note_fields_map(note)
        return [t["ord"] for t in nt["tmpls"] if template_is_nonempty(nt, t, fmap, t["ord"])]

    def note_is_duplicate_or_empty(self, note: Note) -> str | None:
        """Return 'empty' / 'duplicate' / None for the first field, like Anki's add dialog."""
        if not note.fields or not strip_html_media(note.fields[0]).strip():
            return "empty"
        csum = field_checksum(note.fields[0])
        first = strip_html_media(note.fields[0])
        for nid, flds in self.db.execute(
            "SELECT id, flds FROM notes WHERE csum=? AND mid=? AND id!=?", (csum, note.mid, note.id)
        ):
            if strip_html_media(flds.split("\x1f")[0]) == first:
                return "duplicate"
        return None

    def add_note(self, note: Note, did: int) -> int:
        """Add a note and generate its cards. Returns the number of cards created."""
        deck = self.decks.get(str(did))
        if deck is None or deck["dyn"]:
            raise ValueError("Notes can only be added to normal decks")
        ords = self.card_ords_for_note(note)
        if not ords:
            raise ValueError("The note would produce no cards; fill in the front side")
        note.id = self._next_id("notes")
        note.mod = int_time()
        note.usn = self.usn()
        self._write_note(note)
        due = self._next_new_position(did)
        nt = self.models[str(note.mid)]
        for ord_ in ords:
            target = did
            if nt.get("type") != consts.MODEL_CLOZE:
                tmpl = nt["tmpls"][ord_]
                if tmpl.get("did") and str(tmpl["did"]) in self.decks and not self.decks[str(tmpl["did"])]["dyn"]:
                    target = int(tmpl["did"])
            self._add_card(note.id, ord_, target, due)
        self.conf["curModel"] = note.mid
        self.save()
        self.sched.clear_queues()
        return len(ords)

    def _next_new_position(self, did: int) -> int:
        pos = int(self.conf.get("nextPos", 1))
        self.conf["nextPos"] = pos + 1
        if self.config_for_deck(did)["new"].get("order", 1) == consts.NEW_INSERT_RANDOM:
            return random.randint(1, max(pos, 1))
        return pos

    def _add_card(self, nid: int, ord_: int, did: int, due: int) -> Card:
        card = Card(
            id=self._next_id("cards"),
            nid=nid,
            did=did,
            ord=ord_,
            mod=int_time(),
            usn=self.usn(),
            due=due,
        )
        self.db.execute(f"INSERT INTO cards ({CARD_COLUMNS}) VALUES ({','.join('?' * 18)})", card.as_row())
        return card

    def update_note(self, note: Note) -> int:
        """Save a note and generate any newly required cards. Returns new card count."""
        note.mod = int_time()
        note.usn = self.usn()
        self._write_note(note)
        created = self.generate_missing_cards(note)
        self.save()
        self.sched.clear_queues()
        return created

    def generate_missing_cards(self, note: Note) -> int:
        """Add cards required by the note's templates that don't exist yet."""
        existing = {
            r[0]: r[1] for r in self.db.execute("SELECT ord, did FROM cards WHERE nid=?", (note.id,))
        }
        created = 0
        missing = [o for o in self.card_ords_for_note(note) if o not in existing]
        if missing:
            row = self.db.execute(
                "SELECT did, odid, due FROM cards WHERE nid=? ORDER BY ord LIMIT 1", (note.id,)
            ).fetchone()
            if row:
                did = row[1] or row[0]
            else:
                did = self.current_deck_id()
            if self.decks[str(did)]["dyn"]:
                did = consts.DEFAULT_DECK_ID
            due = self._next_new_position(did)
            for ord_ in missing:
                self._add_card(note.id, ord_, did, due)
                created += 1
        return created

    def remove_notes(self, nids: list[int]) -> None:
        if not nids:
            return
        for chunk in _chunks(nids):
            q = ",".join(str(int(n)) for n in chunk)
            cids = [r[0] for r in self.db.execute(f"SELECT id FROM cards WHERE nid IN ({q})")]
            self._remove_cards_only(cids)
            self.db.execute(f"DELETE FROM notes WHERE id IN ({q})")
            self.db.executemany(
                "INSERT INTO graves VALUES (?,?,?)", [(self.usn(), n, GRAVE_NOTE) for n in chunk]
            )
        self.save()
        self.sched.clear_queues()

    def _remove_cards_only(self, cids: list[int]) -> None:
        for chunk in _chunks(cids):
            q = ",".join(str(int(c)) for c in chunk)
            # like Anki, review history is kept when cards are deleted
            self.db.execute(f"DELETE FROM cards WHERE id IN ({q})")
            self.db.executemany(
                "INSERT INTO graves VALUES (?,?,?)", [(self.usn(), c, GRAVE_CARD) for c in chunk]
            )

    def remove_cards_and_orphaned_notes(self, cids: list[int]) -> None:
        if not cids:
            return
        nids = set()
        for chunk in _chunks(cids):
            q = ",".join(str(int(c)) for c in chunk)
            nids |= {r[0] for r in self.db.execute(f"SELECT DISTINCT nid FROM cards WHERE id IN ({q})")}
        self._remove_cards_only(cids)
        orphans = [
            n for n in nids if self.db.execute("SELECT 1 FROM cards WHERE nid=? LIMIT 1", (n,)).fetchone() is None
        ]
        if orphans:
            for chunk in _chunks(orphans):
                q = ",".join(str(int(n)) for n in chunk)
                self.db.execute(f"DELETE FROM notes WHERE id IN ({q})")
        self.save()

    def note_count(self) -> int:
        return self.db.execute("SELECT count() FROM notes").fetchone()[0]

    # ------------------------------------------------------------------ cards

    def get_card(self, cid: int) -> Card | None:
        row = self.db.execute(f"SELECT {CARD_COLUMNS} FROM cards WHERE id=?", (cid,)).fetchone()
        return Card.from_row(row) if row else None

    def update_card(self, card: Card, touch: bool = True) -> None:
        if touch:
            card.mod = int_time()
            card.usn = self.usn()
        self.db.execute(
            f"INSERT OR REPLACE INTO cards ({CARD_COLUMNS}) VALUES ({','.join('?' * 18)})",
            card.as_row(),
        )

    def card_count(self) -> int:
        return self.db.execute("SELECT count() FROM cards").fetchone()[0]

    def cards_of_note(self, nid: int) -> list[Card]:
        return [
            Card.from_row(r)
            for r in self.db.execute(f"SELECT {CARD_COLUMNS} FROM cards WHERE nid=? ORDER BY ord", (nid,))
        ]

    def render_card(self, card: Card, note: Note | None = None) -> RenderedCard:
        note = note or self.get_note(card.nid)
        nt = self.models[str(note.mid)]
        if nt.get("type") == consts.MODEL_CLOZE:
            tmpl = nt["tmpls"][0]
        else:
            tmpl = nt["tmpls"][min(card.ord, len(nt["tmpls"]) - 1)]
        fmap = self.note_fields_map(note)
        deck = self.decks.get(str(card.odid or card.did), {"name": ""})
        fmap.update(
            {
                "Tags": " ".join(note.tags),
                "Type": nt["name"],
                "Deck": deck["name"],
                "Subdeck": deck["name"].split("::")[-1],
                "Card": tmpl["name"] if nt.get("type") != consts.MODEL_CLOZE else f"Cloze {card.ord + 1}",
                "CardFlag": f"flag{card.user_flag}" if card.user_flag else "",
                "CardID": str(card.id),
            }
        )
        return render_card(nt, tmpl, fmap, card.ord)

    def set_deck(self, cids: list[int], did: int) -> None:
        deck = self.decks.get(str(did))
        if deck is None or deck["dyn"]:
            raise ValueError("Cards can only be moved to a normal deck")
        for cid in cids:
            card = self.get_card(cid)
            if card is None:
                continue
            if card.odid:
                card.odid = did
            else:
                card.did = did
            self.update_card(card)
        self.save()
        self.sched.clear_queues()

    # ------------------------------------------------------------------- tags

    def register_tags(self, tags: list[str]) -> None:
        known = {t.lower() for t in self.tags}
        for tag in tags:
            if tag.lower() not in known:
                self.tags[tag] = self.usn()
                known.add(tag.lower())

    def all_tags(self) -> list[str]:
        return sorted(self.tags, key=str.lower)

    def tags_in_decks(self, dids: list[int]) -> list[str]:
        q = ",".join(str(int(d)) for d in dids) or "0"
        found: dict[str, str] = {}
        for (tags,) in self.db.execute(
            f"SELECT DISTINCT n.tags FROM notes n JOIN cards c ON c.nid=n.id WHERE c.did IN ({q}) OR c.odid IN ({q})"
        ):
            for t in tags.split():
                found.setdefault(t.lower(), t)
        return sorted(found.values(), key=str.lower)

    def add_tags_to_notes(self, nids: list[int], tag: str) -> None:
        for nid in nids:
            note = self.get_note(nid)
            if note and not note.has_tag(tag):
                note.add_tag(tag)
                note.mod = int_time()
                note.usn = self.usn()
                self._write_note(note)
        self.save()

    def remove_tags_from_notes(self, nids: list[int], tag: str) -> None:
        for nid in nids:
            note = self.get_note(nid)
            if note and note.has_tag(tag):
                note.remove_tag(tag)
                note.mod = int_time()
                note.usn = self.usn()
                self._write_note(note)
        self.save()

    # ----------------------------------------------------------------- search

    def find_cards(self, query: str, order: str | None = None) -> list[int]:
        from .search import build_card_search

        sql, args = build_card_search(self, query, order)
        return [r[0] for r in self.db.execute(sql, args)]

    def find_notes(self, query: str) -> list[int]:
        from .search import build_where

        where, args = build_where(self, query)
        sql = f"SELECT DISTINCT n.id FROM cards c JOIN notes n ON c.nid=n.id WHERE {where} ORDER BY n.id"
        return [r[0] for r in self.db.execute(sql, args)]


def _chunks(items: list, size: int = 500):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i : i + size]
