"""Card template rendering (a subset of Anki's template language).

Supports field replacements, conditionals ({{#F}}/{{^F}}), {{FrontSide}},
special fields, filters (text, cloze, hint, type, furigana, kana, kanji) and
cloze deletions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .utils import strip_html

_TAG_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.S)
TYPE_ANSWER_MARKER = "\x00TYPEANS\x00"
_FURIGANA_RE = re.compile(r" ?([^ >]+?)\[(.+?)\]")


@dataclass
class _Node:
    kind: str  # "text", "var", "section", "inverted"
    value: str = ""
    children: list["_Node"] = field(default_factory=list)


class TemplateError(Exception):
    pass


def _parse(template: str) -> list[_Node]:
    root: list[_Node] = []
    stack: list[tuple[str, list[_Node]]] = [("", root)]
    pos = 0
    for m in _TAG_RE.finditer(template):
        if m.start() > pos:
            stack[-1][1].append(_Node("text", template[pos : m.start()]))
        pos = m.end()
        tag = m.group(1).strip()
        if not tag:
            continue
        if tag[0] in "#^":
            node = _Node("section" if tag[0] == "#" else "inverted", tag[1:].strip())
            stack[-1][1].append(node)
            stack.append((node.value, node.children))
        elif tag[0] == "/":
            name = tag[1:].strip()
            if len(stack) == 1 or stack[-1][0] != name:
                raise TemplateError(f"Unexpected closing tag {{{{/{name}}}}}")
            stack.pop()
        else:
            stack[-1][1].append(_Node("var", tag))
    if pos < len(template):
        stack[-1][1].append(_Node("text", template[pos:]))
    if len(stack) > 1:
        raise TemplateError(f"Missing closing tag for {{{{#{stack[-1][0]}}}}}")
    return root


def field_is_empty(text: str) -> bool:
    return not re.sub(r"(?i)<br\s*/?>|<div>|</div>|&nbsp;|\s", "", text)


@dataclass
class RenderContext:
    fields: dict[str, str]
    card_ord: int
    is_cloze: bool
    question: bool
    front_side: str = ""
    nonempty_used: bool = False
    type_field: str | None = None
    type_cloze: bool = False


def _cloze_numbers(text: str) -> set[int]:
    return {int(m.group(1)) for m in CLOZE_RE.finditer(text)}


def cloze_numbers_in_fields(notetype: dict, fields: list[str]) -> set[int]:
    names = [f["name"] for f in sorted(notetype["flds"], key=lambda f: f["ord"])]
    referenced = set()
    for tmpl in notetype["tmpls"]:
        for m in _TAG_RE.finditer(tmpl["qfmt"]):
            parts = m.group(1).strip().split(":")
            if "cloze" in parts[:-1]:
                referenced.add(parts[-1].strip())
    nums: set[int] = set()
    for name, value in zip(names, fields):
        if name in referenced:
            nums |= _cloze_numbers(value)
    return nums


def render_cloze(text: str, ord_: int, question: bool) -> str:
    target = ord_ + 1

    def repl(m: re.Match) -> str:
        num = int(m.group(1))
        content = m.group(2)
        hint = m.group(3)
        if num != target:
            return content
        if question:
            label = hint if hint else "..."
            return f'<span class="cloze">[{label}]</span>'
        return f'<span class="cloze">{content}</span>'

    # Nested clozes are resolved from the inside out.
    prev = None
    while prev != text:
        prev = text
        text = CLOZE_RE.sub(repl, text)
    return text


def cloze_answer_text(text: str, ord_: int) -> str:
    target = ord_ + 1
    return ", ".join(
        strip_html(m.group(2)) for m in CLOZE_RE.finditer(text) if int(m.group(1)) == target
    )


def _furigana(text: str) -> str:
    return _FURIGANA_RE.sub(r"<ruby>\1<rt>\2</rt></ruby>", text)


def _kana(text: str) -> str:
    return _FURIGANA_RE.sub(r"\2", text)


def _kanji(text: str) -> str:
    return _FURIGANA_RE.sub(r"\1", text)


def _apply_filters(name: str, filters: list[str], value: str, ctx: RenderContext) -> str:
    for flt in reversed(filters):
        flt = flt.strip()
        if flt == "text":
            value = strip_html(value)
        elif flt == "cloze":
            if ctx.is_cloze:
                value = render_cloze(value, ctx.card_ord, ctx.question)
        elif flt == "hint":
            value = f'<span class="hint">Hint: {value}</span>' if value else ""
        elif flt == "furigana":
            value = _furigana(value)
        elif flt == "kana":
            value = _kana(value)
        elif flt == "kanji":
            value = _kanji(value)
        elif flt.startswith("tts") or flt == "type":
            value = ""
        # unknown filters are ignored, as Anki does for add-on filters
    return value


def _render_var(tag: str, ctx: RenderContext) -> str:
    parts = tag.split(":")
    name = parts[-1].strip()
    filters = parts[:-1]
    if name == "FrontSide":
        return ctx.front_side if not ctx.question else ""
    if filters and filters[0].strip() == "type":
        is_cloze = len(filters) > 1 and filters[1].strip() == "cloze"
        if name in ctx.fields:
            ctx.type_field = name
            ctx.type_cloze = is_cloze
            return TYPE_ANSWER_MARKER
        return f"(type answer: unknown field {name})"
    if name not in ctx.fields:
        return f"{{{{{tag}}}}}: unknown field"
    value = ctx.fields[name]
    if name not in _SPECIAL_FIELDS and not field_is_empty(value):
        ctx.nonempty_used = True
    return _apply_filters(name, filters, value, ctx)


_SPECIAL_FIELDS = {"Tags", "Type", "Deck", "Subdeck", "Card", "CardFlag", "CardID"}


def _render_nodes(nodes: list[_Node], ctx: RenderContext) -> str:
    out = []
    for node in nodes:
        if node.kind == "text":
            out.append(node.value)
        elif node.kind == "var":
            out.append(_render_var(node.value, ctx))
        else:
            value = ctx.fields.get(node.value, "")
            nonempty = not field_is_empty(value)
            if node.kind == "section" and node.value.startswith("cloze:"):
                # {{#cloze:Text}} is true if the field has the current cloze
                fname = node.value.split(":", 1)[1]
                nonempty = (ctx.card_ord + 1) in _cloze_numbers(ctx.fields.get(fname, ""))
            if (node.kind == "section") == nonempty:
                out.append(_render_nodes(node.children, ctx))
    return "".join(out)


@dataclass
class RenderedCard:
    question: str
    answer: str
    question_nonempty: bool
    type_field: str | None = None
    type_cloze: bool = False
    css: str = ""


def render_card(
    notetype: dict,
    template: dict,
    fields: dict[str, str],
    card_ord: int,
) -> RenderedCard:
    is_cloze = notetype.get("type") == 1
    try:
        q_nodes = _parse(template["qfmt"])
        a_nodes = _parse(template["afmt"])
    except TemplateError as err:
        msg = f"Template error: {err}"
        return RenderedCard(msg, msg, True)
    qctx = RenderContext(fields, card_ord, is_cloze, question=True)
    question = _render_nodes(q_nodes, qctx)
    actx = RenderContext(fields, card_ord, is_cloze, question=False, front_side=question)
    answer = _render_nodes(a_nodes, actx)
    if is_cloze and qctx.nonempty_used:
        # a cloze card needs its cloze number present in some field
        nums: set[int] = set()
        for v in fields.values():
            nums |= _cloze_numbers(v)
        qctx.nonempty_used = (card_ord + 1) in nums
    return RenderedCard(
        question=question,
        answer=answer,
        question_nonempty=qctx.nonempty_used,
        type_field=qctx.type_field or actx.type_field,
        type_cloze=qctx.type_cloze or actx.type_cloze,
        css=notetype.get("css", ""),
    )


def template_is_nonempty(notetype: dict, template: dict, fields: dict[str, str], card_ord: int) -> bool:
    try:
        nodes = _parse(template["qfmt"])
    except TemplateError:
        return False
    ctx = RenderContext(fields, card_ord, notetype.get("type") == 1, question=True)
    _render_nodes(nodes, ctx)
    return ctx.nonempty_used


def compare_typed_answer(given: str, correct: str) -> list[tuple[str, str]]:
    """Return a list of (style, text) segments describing a typed-answer diff.

    style is one of "good", "bad" or "missing", like Anki's typeGood/typeBad/typeMissed.
    """
    import difflib

    given = given.strip()
    correct = correct.strip()
    if not given:
        return [("missing", correct)]
    segments: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(None, given, correct, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            segments.append(("good", given[i1:i2]))
        elif op == "delete":
            segments.append(("bad", given[i1:i2]))
        elif op == "insert":
            segments.append(("missing", correct[j1:j2]))
        else:
            segments.append(("bad", given[i1:i2]))
            segments.append(("missing", correct[j1:j2]))
    return segments
