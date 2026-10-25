"""Convert card HTML into Rich text for display in the terminal."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from rich.style import Style
from rich.text import Text

from ..core.template import TYPE_ANSWER_MARKER

_BLOCK_TAGS = {"div", "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote", "pre"}
_SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
_COLOR_RE = re.compile(r"(?:^|;)\s*color\s*:\s*([^;]+)", re.I)
_BG_RE = re.compile(r"background(?:-color)?\s*:\s*([^;]+)", re.I)
_WEIGHT_RE = re.compile(r"font-weight\s*:\s*(bold|[6-9]00)", re.I)
_ITALIC_RE = re.compile(r"font-style\s*:\s*italic", re.I)

CLOZE_STYLE = Style(bold=True, color="bright_blue")
HINT_STYLE = Style(dim=True, italic=True)


def _css_color(value: str) -> str | None:
    value = value.strip().strip("'\"").lower()
    if not value:
        return None
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    m = re.fullmatch(r"rgba?\((\d+),\s*(\d+),\s*(\d+).*\)", value)
    if m:
        value = "#%02x%02x%02x" % tuple(int(g) for g in m.groups())
    try:
        Style.parse(value)
        return value
    except Exception:
        return None


class _Renderer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text = Text()
        self.styles: list[tuple[str, Style | None]] = []
        self.skip_depth = 0
        self.list_stack: list[list] = []
        self.pending_space = False
        self.in_pre = 0
        self.ruby_rt = False

    # helpers
    def _style(self) -> Style:
        style = Style()
        for _, s in self.styles:
            if s is not None:
                style += s
        return style

    def _newline(self, count: int = 1) -> None:
        plain = self.text.plain
        trailing = len(plain) - len(plain.rstrip("\n"))
        if not plain:
            return
        if trailing < count:
            self.text.append("\n" * (count - trailing))
        self.pending_space = False

    def _append(self, data: str, style: Style | None = None) -> None:
        if not data:
            return
        self.text.append(data, style or self._style())

    # parser callbacks
    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag in ("style", "script", "head", "title"):
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        style_attr = attrs.get("style") or ""
        css_style = Style()
        m = _COLOR_RE.search(style_attr)
        if m and _css_color(m.group(1)):
            css_style += Style(color=_css_color(m.group(1)))
        m = _BG_RE.search(style_attr)
        if m and _css_color(m.group(1)):
            css_style += Style(bgcolor=_css_color(m.group(1)))
        if _WEIGHT_RE.search(style_attr):
            css_style += Style(bold=True)
        if _ITALIC_RE.search(style_attr):
            css_style += Style(italic=True)

        if tag == "br":
            self.text.append("\n")
            self.pending_space = False
            return
        if tag == "hr":
            self._newline()
            self._append("─" * 40, Style(dim=True))
            self.text.append("\n")
            return
        if tag == "img":
            src = attrs.get("src", "")
            self._append(f"[image: {src}]", Style(color="magenta", italic=True))
            return
        if tag in _BLOCK_TAGS:
            self._newline()
        if tag == "ul":
            self.list_stack.append(["ul", 0])
        elif tag == "ol":
            self.list_stack.append(["ol", 0])
        elif tag == "li":
            depth = max(len(self.list_stack) - 1, 0)
            if self.list_stack and self.list_stack[-1][0] == "ol":
                self.list_stack[-1][1] += 1
                bullet = f"{self.list_stack[-1][1]}. "
            else:
                bullet = "• "
            self._append("  " * depth + bullet)
        elif tag in ("td", "th"):
            if not self.text.plain.endswith("\n") and self.text.plain:
                self._append(" │ ", Style(dim=True))
        elif tag == "pre":
            self.in_pre += 1
        elif tag == "rt":
            self.ruby_rt = True
            self._append("(")
        style: Style | None = None
        if tag in ("b", "strong", "th") or tag.startswith("h") and tag[1:].isdigit():
            style = Style(bold=True)
        elif tag in ("i", "em"):
            style = Style(italic=True)
        elif tag == "u":
            style = Style(underline=True)
        elif tag in ("s", "strike", "del"):
            style = Style(strike=True)
        elif tag == "a":
            style = Style(underline=True, color="cyan")
        elif tag in ("code", "kbd", "pre"):
            style = Style(color="bright_white", bgcolor="grey19")
        elif tag == "font":
            color = _css_color(attrs.get("color", ""))
            style = Style(color=color) if color else None
        elif tag == "span":
            classes = (attrs.get("class") or "").split()
            if "cloze" in classes:
                style = CLOZE_STYLE
            elif "hint" in classes:
                style = HINT_STYLE
        if css_style:
            style = (style or Style()) + css_style
        if tag not in ("br", "hr", "img"):
            self.styles.append((tag, style))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "head", "title"):
            self.skip_depth = max(self.skip_depth - 1, 0)
            return
        if self.skip_depth:
            return
        if tag == "rt":
            self._append(")")
            self.ruby_rt = False
        for i in range(len(self.styles) - 1, -1, -1):
            if self.styles[i][0] == tag:
                del self.styles[i:]
                break
        if tag in ("ul", "ol") and self.list_stack:
            self.list_stack.pop()
        if tag == "pre":
            self.in_pre = max(self.in_pre - 1, 0)
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if not self.in_pre:
            data = re.sub(r"\s+", " ", data)
            if self.text.plain.endswith(("\n", " ")) or not self.text.plain:
                data = data.lstrip(" ")
        pos = 0
        for m in _SOUND_RE.finditer(data):
            self._append(data[pos : m.start()])
            self._append(f"♪ {m.group(1)}", Style(color="green", italic=True))
            pos = m.end()
        self._append(data[pos:])


def html_to_text(html: str, justify: str = "center") -> Text:
    renderer = _Renderer()
    renderer.feed(html)
    renderer.close()
    text = renderer.text
    lines = text.split("\n")
    # trim blank lines at both ends, and collapse runs of blank lines
    out = Text(justify=justify)
    blank = 0
    started = False
    for line in lines:
        line.rstrip()
        if not line.plain.strip():
            blank += 1
            continue
        if started:
            out.append("\n" * min(blank + 1, 2))
        blank = 0
        started = True
        out.append_text(line)
    out.justify = justify
    return out


def split_type_marker(html: str) -> tuple[str, str]:
    """Split rendered HTML at the type-in-answer marker."""
    if TYPE_ANSWER_MARKER in html:
        before, after = html.split(TYPE_ANSWER_MARKER, 1)
        return before, after
    return html, ""
