"""A port of the commonly used subset of Anki's search syntax to SQL.

Supported: plain text, "quoted phrases", field:value, deck:, tag:, is:,
card:, note:, flag:, rated:, prop:, added:, nid:, cid:, negation (-),
grouping with parentheses, and/or.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from . import consts

if TYPE_CHECKING:
    from .collection import Collection


class SearchError(Exception):
    pass


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()":
            tokens.append(ch)
            i += 1
            continue
        if ch == "-" and (i + 1 < n and not text[i + 1].isspace()):
            tokens.append("-")
            i += 1
            continue
        buf = []
        in_quote = False
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                buf.append(text[i : i + 2])
                i += 2
                continue
            if ch == '"':
                in_quote = not in_quote
                i += 1
                continue
            if not in_quote and (ch.isspace() or ch in "()"):
                break
            buf.append(ch)
            i += 1
        if in_quote:
            raise SearchError("Unterminated quote in search")
        tokens.append("".join(buf))
    return tokens


def _like_pattern(text: str) -> str:
    """Convert Anki wildcards (* and _) to a LIKE pattern with \\ as escape."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append("\\" + nxt if nxt in "%_\\" else nxt)
            i += 2
            continue
        if ch == "*":
            out.append("%")
        elif ch == "%":
            out.append("\\%")
        elif ch == "_":
            out.append("_")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _glob_to_regex(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(re.escape(text[i + 1]))
            i += 2
            continue
        if ch == "*":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return "^" + "".join(out) + "$"


def escape_search_value(text: str) -> str:
    """Escape a literal value (e.g. a deck name) for use in a quoted search term."""
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace('"', '\\"')


class _Parser:
    def __init__(self, col: "Collection", tokens: list[str]):
        self.col = col
        self.tokens = tokens
        self.pos = 0
        self.args: list = []
        self.timing = col.timing_today()

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> str:
        if not self.tokens:
            return "1"
        sql = self.parse_or()
        if self.peek() is not None:
            raise SearchError(f"Unexpected '{self.peek()}' in search")
        return sql

    def parse_or(self) -> str:
        parts = [self.parse_and()]
        while self.peek() is not None and self.peek().lower() == "or":
            self.take()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    def parse_and(self) -> str:
        parts = []
        while True:
            tok = self.peek()
            if tok is None or tok == ")" or tok.lower() == "or":
                break
            if tok.lower() == "and":
                self.take()
                continue
            parts.append(self.parse_unary())
        if not parts:
            raise SearchError("Empty search group")
        return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"

    def parse_unary(self) -> str:
        tok = self.take()
        if tok == "-":
            return f"(NOT {self.parse_unary()})"
        if tok == "(":
            inner = self.parse_or()
            if self.peek() != ")":
                raise SearchError("Missing closing parenthesis")
            self.take()
            return f"({inner})"
        return self.term(tok)

    # ---------------------------------------------------------------- terms

    def term(self, tok: str) -> str:
        if ":" in tok and not tok.startswith(":"):
            key, value = tok.split(":", 1)
            handler = getattr(self, "t_" + key.lower(), None)
            if handler is not None:
                return handler(value)
            return self.t_field(key, value)
        return self.t_text(tok)

    def _arg(self, value) -> str:
        self.args.append(value)
        return "?"

    def t_text(self, text: str) -> str:
        if not text:
            return "1"
        pat = "%" + _like_pattern(text) + "%"
        return f"(n.flds LIKE {self._arg(pat)} ESCAPE '\\')"

    def t_field(self, name: str, value: str) -> str:
        regex = re.compile(_glob_to_regex(value), re.I | re.S)
        name_re = re.compile(_glob_to_regex(name), re.I)
        matches: list[tuple[int, int]] = []
        for nt in self.col.models.values():
            for f in nt["flds"]:
                if name_re.match(f["name"]):
                    matches.append((int(nt["id"]), f["ord"]))
        if not matches:
            return "0"
        nids = []
        mids = {m for m, _ in matches}
        q = ",".join(str(m) for m in mids)
        ords_by_mid: dict[int, list[int]] = {}
        for mid, ord_ in matches:
            ords_by_mid.setdefault(mid, []).append(ord_)
        for nid, mid, flds in self.col.db.execute(f"SELECT id, mid, flds FROM notes WHERE mid IN ({q})"):
            parts = flds.split("\x1f")
            for ord_ in ords_by_mid.get(mid, []):
                if ord_ < len(parts) and regex.match(parts[ord_]):
                    nids.append(nid)
                    break
        if not nids:
            return "0"
        return f"(n.id IN ({','.join(str(n) for n in nids)}))"

    def t_deck(self, value: str) -> str:
        if value == "*":
            return "1"
        if value.lower() == "filtered":
            return "(c.odid != 0)"
        if value.lower() == "current":
            ids = self.col.child_ids(self.col.current_deck_id(), include_self=True)
        else:
            regex = re.compile(_glob_to_regex(value), re.I)
            ids = []
            for deck in self.col.decks.values():
                if regex.match(deck["name"]):
                    ids.extend(self.col.child_ids(int(deck["id"]), include_self=True))
        if not ids:
            return "0"
        q = ",".join(str(i) for i in sorted(set(ids)))
        return f"(c.did IN ({q}) OR c.odid IN ({q}))"

    def t_tag(self, value: str) -> str:
        if value == "none":
            return "(n.tags = '')"
        if value == "*":
            return "(n.tags != '')"
        regex = _like_pattern(value)
        a = self._arg(f"% {regex} %")
        b = self._arg(f"% {regex}::%")
        return f"(n.tags LIKE {a} ESCAPE '\\' OR n.tags LIKE {b} ESCAPE '\\')"

    def t_is(self, value: str) -> str:
        v = value.lower()
        today = self.timing.days_elapsed
        if v == "new":
            return f"(c.type = {consts.CARD_TYPE_NEW})"
        if v == "learn":
            return f"(c.queue IN ({consts.QUEUE_LRN},{consts.QUEUE_DAY_LRN}))"
        if v == "review":
            return f"(c.type IN ({consts.CARD_TYPE_REV},{consts.CARD_TYPE_RELEARNING}))"
        if v == "due":
            learn_cutoff = int(time.time()) + int(self.col.conf.get("collapseTime", 1200))
            return (
                f"((c.queue IN ({consts.QUEUE_REV},{consts.QUEUE_DAY_LRN}) AND "
                f"(CASE WHEN c.odue != 0 THEN c.odue ELSE c.due END) <= {today}) OR "
                f"(c.queue IN ({consts.QUEUE_LRN},{consts.QUEUE_PREVIEW}) AND "
                f"(CASE WHEN c.odue != 0 THEN c.odue ELSE c.due END) <= {learn_cutoff}))"
            )
        if v == "suspended":
            return f"(c.queue = {consts.QUEUE_SUSPENDED})"
        if v == "buried":
            return f"(c.queue IN ({consts.QUEUE_SCHED_BURIED},{consts.QUEUE_USER_BURIED}))"
        if v == "buried-sibling":
            return f"(c.queue = {consts.QUEUE_SCHED_BURIED})"
        if v == "buried-manually":
            return f"(c.queue = {consts.QUEUE_USER_BURIED})"
        if v == "marked":
            return self.t_tag("marked")
        if v == "leech":
            return self.t_tag("leech")
        raise SearchError(f"Unknown search: is:{value}")

    def t_flag(self, value: str) -> str:
        try:
            flag = int(value)
        except ValueError as err:
            raise SearchError("flag: requires a number 0-7") from err
        return f"((c.flags & 7) = {flag})"

    def t_card(self, value: str) -> str:
        if value.isdigit():
            return f"(c.ord = {int(value) - 1})"
        regex = re.compile(_glob_to_regex(value), re.I)
        conds = []
        for nt in self.col.models.values():
            if nt.get("type") == consts.MODEL_CLOZE:
                continue
            for t in nt["tmpls"]:
                if regex.match(t["name"]):
                    conds.append(f"(n.mid = {int(nt['id'])} AND c.ord = {t['ord']})")
        return "(" + " OR ".join(conds) + ")" if conds else "0"

    def t_note(self, value: str) -> str:
        regex = re.compile(_glob_to_regex(value), re.I)
        mids = [str(int(m["id"])) for m in self.col.models.values() if regex.match(m["name"])]
        return f"(n.mid IN ({','.join(mids)}))" if mids else "0"

    def t_mid(self, value: str) -> str:
        return f"(n.mid = {int(value)})"

    def t_nid(self, value: str) -> str:
        ids = ",".join(str(int(v)) for v in value.split(",") if v.strip())
        return f"(n.id IN ({ids}))"

    def t_cid(self, value: str) -> str:
        ids = ",".join(str(int(v)) for v in value.split(",") if v.strip())
        return f"(c.id IN ({ids}))"

    def _days_cutoff_ms(self, days: int) -> int:
        return (self.timing.next_day_at - 86_400 * days) * 1000

    def t_rated(self, value: str) -> str:
        parts = value.split(":")
        try:
            days = max(min(int(parts[0]), 36500), 1)
        except ValueError as err:
            raise SearchError("rated: requires a number of days") from err
        cutoff = self._days_cutoff_ms(days)
        ease_clause = "ease > 0"
        if len(parts) > 1:
            ease_clause = f"ease = {int(parts[1])}"
        return (
            f"(c.id IN (SELECT cid FROM revlog WHERE id > {cutoff} AND {ease_clause} "
            f"AND type != {consts.REVLOG_MANUAL}))"
        )

    def t_added(self, value: str) -> str:
        try:
            days = max(int(value), 1)
        except ValueError as err:
            raise SearchError("added: requires a number of days") from err
        return f"(c.id > {self._days_cutoff_ms(days)})"

    def t_introduced(self, value: str) -> str:
        days = max(int(value), 1)
        cutoff = self._days_cutoff_ms(days)
        return (
            f"(c.id IN (SELECT cid FROM revlog GROUP BY cid HAVING min(id) > {cutoff}) "
            f"AND c.type != {consts.CARD_TYPE_NEW})"
        )

    def t_prop(self, value: str) -> str:
        m = re.match(r"^(ivl|due|reps|lapses|ease|pos)(<=|>=|!=|=|<|>)(-?[\d.]+)$", value, re.I)
        if not m:
            raise SearchError(f"Invalid property search: prop:{value}")
        prop, op, num = m.group(1).lower(), m.group(2), m.group(3)
        if prop == "due":
            day = self.timing.days_elapsed + int(float(num))
            return (
                f"(c.queue IN ({consts.QUEUE_REV},{consts.QUEUE_DAY_LRN}) AND "
                f"(CASE WHEN c.odue != 0 THEN c.odue ELSE c.due END) {op} {day})"
            )
        if prop == "ease":
            return f"(c.factor {op} {int(float(num) * 1000)})"
        if prop == "pos":
            return f"(c.type = 0 AND c.due {op} {int(float(num))})"
        return f"(c.{prop} {op} {int(float(num))})"


def build_where(col: "Collection", query: str) -> tuple[str, list]:
    parser = _Parser(col, _tokenize(query))
    return parser.parse(), parser.args


ORDER_CLAUSES = {
    "noteCrt": "n.id, c.ord",
    "noteMod": "n.mod",
    "cardMod": "c.mod",
    "cardDue": "c.type, (CASE WHEN c.odue != 0 THEN c.odue ELSE c.due END)",
    "cardIvl": "c.ivl",
    "cardEase": "c.factor",
    "cardReps": "c.reps",
    "cardLapses": "c.lapses",
    "noteFld": "n.sfld COLLATE NOCASE, c.ord",
}


def build_card_search(col: "Collection", query: str, order: str | None) -> tuple[str, list]:
    where, args = build_where(col, query)
    sql = f"SELECT c.id FROM cards c JOIN notes n ON c.nid = n.id WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    return sql, args


def filtered_order_sql(order: int, today: int, next_day_at: int) -> str:
    """Port of order_and_limit_for_search() for filtered deck building."""
    if order == consts.DYN_OLDEST:
        return "(SELECT max(id) FROM revlog WHERE cid = c.id)"
    if order == consts.DYN_RANDOM:
        return "random()"
    if order == consts.DYN_SMALLINT:
        return "c.ivl ASC"
    if order == consts.DYN_BIGINT:
        return "c.ivl DESC"
    if order == consts.DYN_LAPSES:
        return "c.lapses DESC"
    if order == consts.DYN_ADDED:
        return "n.id, c.ord"
    if order == consts.DYN_REVADDED:
        return "n.id DESC, c.ord"
    if order == consts.DYN_DUE:
        return (
            "c.type, (CASE WHEN c.queue IN (1,4) THEN c.due - "
            f"{next_day_at} ELSE (c.due - {today}) * 86400 END), c.ord"
        )
    if order == consts.DYN_DUEPRIORITY:
        return (
            f"(CASE WHEN c.queue = 2 AND c.due <= {today} THEN "
            f"({today} - c.due + 0.001) / max(c.ivl, 1) ELSE 0 END) DESC, c.ord"
        )
    return "random()"
