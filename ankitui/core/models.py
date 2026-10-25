"""Card/Note records and the stock note types (schema 11 JSON)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .consts import MODEL_CLOZE, MODEL_STD
from .utils import join_fields, join_tags, parse_tags, split_fields

CARD_COLUMNS = (
    "id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, "
    "left, odue, odid, flags, data"
)
NOTE_COLUMNS = "id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data"


@dataclass
class Card:
    id: int = 0
    nid: int = 0
    did: int = 0
    ord: int = 0
    mod: int = 0
    usn: int = -1
    type: int = 0
    queue: int = 0
    due: int = 0
    ivl: int = 0
    factor: int = 0
    reps: int = 0
    lapses: int = 0
    left: int = 0
    odue: int = 0
    odid: int = 0
    flags: int = 0
    data: str = ""

    @classmethod
    def from_row(cls, row) -> "Card":
        return cls(*row)

    def as_row(self) -> tuple:
        return (
            self.id, self.nid, self.did, self.ord, self.mod, self.usn, self.type,
            self.queue, self.due, self.ivl, self.factor, self.reps, self.lapses,
            self.left, self.odue, self.odid, self.flags, self.data,
        )

    def copy(self) -> "Card":
        return copy.copy(self)

    @property
    def user_flag(self) -> int:
        return self.flags & 0b111

    def set_user_flag(self, flag: int) -> None:
        self.flags = (self.flags & ~0b111) | (flag & 0b111)

    def current_deck_id(self) -> int:
        return self.odid or self.did


@dataclass
class Note:
    id: int = 0
    guid: str = ""
    mid: int = 0
    mod: int = 0
    usn: int = -1
    tags: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    flags: int = 0
    data: str = ""

    @classmethod
    def from_row(cls, row) -> "Note":
        nid, guid, mid, mod, usn, tags, flds, _sfld, _csum, flags, data = row
        return cls(nid, guid, mid, mod, usn, parse_tags(tags), split_fields(flds), flags, data)

    @property
    def flds(self) -> str:
        return join_fields(self.fields)

    @property
    def tags_str(self) -> str:
        return join_tags(self.tags)

    def has_tag(self, tag: str) -> bool:
        return tag.lower() in (t.lower() for t in self.tags)

    def add_tag(self, tag: str) -> None:
        if not self.has_tag(tag):
            self.tags.append(tag)

    def remove_tag(self, tag: str) -> None:
        self.tags = [t for t in self.tags if t.lower() != tag.lower()]

    def copy(self) -> "Note":
        return Note(
            self.id, self.guid, self.mid, self.mod, self.usn, list(self.tags),
            list(self.fields), self.flags, self.data,
        )


DEFAULT_CSS = """.card {
    font-family: arial;
    font-size: 20px;
    line-height: 1.5;
    text-align: center;
    color: black;
    background-color: white;
}
"""

CLOZE_CSS = DEFAULT_CSS + """
.cloze {
    font-weight: bold;
    color: blue;
}
.nightMode .cloze {
    color: lightblue;
}
"""


def new_field(name: str, ord_: int) -> dict:
    return {
        "name": name,
        "ord": ord_,
        "sticky": False,
        "rtl": False,
        "font": "Arial",
        "size": 20,
        "description": "",
        "plainText": False,
        "collapsed": False,
        "excludeFromSearch": False,
        "media": [],
    }


def new_template(name: str, ord_: int, qfmt: str, afmt: str) -> dict:
    return {
        "name": name,
        "ord": ord_,
        "qfmt": qfmt,
        "afmt": afmt,
        "bqfmt": "",
        "bafmt": "",
        "did": None,
        "bfont": "",
        "bsize": 0,
    }


def new_notetype(name: str, kind: int = MODEL_STD) -> dict:
    return {
        "id": 0,
        "name": name,
        "type": kind,
        "mod": 0,
        "usn": -1,
        "sortf": 0,
        "did": None,
        "tmpls": [],
        "flds": [],
        "css": CLOZE_CSS if kind == MODEL_CLOZE else DEFAULT_CSS,
        "latexPre": "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n"
        "\\usepackage[utf8]{inputenc}\n\\usepackage{amssymb,amsmath}\n"
        "\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n",
        "latexPost": "\\end{document}",
        "latexsvg": False,
        "req": [],
        "tags": [],
        "vers": [],
    }


def _basic(name: str = "Basic") -> dict:
    nt = new_notetype(name)
    nt["flds"] = [new_field("Front", 0), new_field("Back", 1)]
    nt["tmpls"] = [
        new_template("Card 1", 0, "{{Front}}", "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}")
    ]
    return nt


def stock_notetypes() -> list[dict]:
    basic = _basic()

    rev = _basic("Basic (and reversed card)")
    rev["tmpls"].append(
        new_template("Card 2", 1, "{{Back}}", "{{FrontSide}}\n\n<hr id=answer>\n\n{{Front}}")
    )

    opt = _basic("Basic (optional reversed card)")
    opt["flds"].append(new_field("Add Reverse", 2))
    opt["tmpls"].append(
        new_template(
            "Card 2",
            1,
            "{{#Add Reverse}}{{Back}}{{/Add Reverse}}",
            "{{FrontSide}}\n\n<hr id=answer>\n\n{{Front}}",
        )
    )

    typing = _basic("Basic (type in the answer)")
    typing["tmpls"][0]["qfmt"] = "{{Front}}\n\n{{type:Back}}"
    typing["tmpls"][0]["afmt"] = "{{Front}}\n\n<hr id=answer>\n\n{{type:Back}}"

    cloze = new_notetype("Cloze", MODEL_CLOZE)
    cloze["flds"] = [new_field("Text", 0), new_field("Back Extra", 1)]
    cloze["tmpls"] = [
        new_template("Cloze", 0, "{{cloze:Text}}", "{{cloze:Text}}<br>\n{{Back Extra}}")
    ]
    return [basic, rev, opt, typing, cloze]


def field_names(notetype: dict) -> list[str]:
    return [f["name"] for f in sorted(notetype["flds"], key=lambda f: f["ord"])]
