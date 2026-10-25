"""Terminal charts built from Unicode block characters."""

from __future__ import annotations

from datetime import date, timedelta

from rich.text import Text

_EIGHTHS = " ▁▂▃▄▅▆▇█"


def _fmt(n: float) -> str:
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return f"{n:.0f}" if n == int(n) else f"{n:.1f}"


def bin_columns(columns: list[list[float]], max_cols: int) -> tuple[list[list[float]], int]:
    """Merge adjacent columns so that at most max_cols remain. Returns (columns, group size)."""
    if len(columns) <= max_cols or max_cols <= 0:
        return columns, 1
    size = -(-len(columns) // max_cols)
    merged = []
    for i in range(0, len(columns), size):
        group = columns[i : i + size]
        merged.append([sum(vals) for vals in zip(*group)])
    return merged, size


def stacked_bars(
    columns: list[list[float]],
    colors: list[str],
    width: int,
    height: int = 8,
    x_labels: tuple[str, str] | None = None,
) -> Text:
    """Vertical stacked bar chart. Each column is a list of segment values (bottom first)."""
    axis_w = 6
    avail = max(width - axis_w - 1, 10)
    cols, _ = bin_columns(columns, avail)
    n = max(len(cols), 1)
    # spread the available width over the columns; wide columns get a 1-cell gap
    widths = [(i + 1) * avail // n - i * avail // n for i in range(n)]
    gap = 1 if min(widths) >= 3 else 0
    totals = [sum(c) for c in cols]
    peak = max(totals) if totals and max(totals) > 0 else 1
    units = height * 8
    out = Text()
    for row in range(height, 0, -1):
        label = _fmt(peak) if row == height else (_fmt(peak / 2) if row == (height + 1) // 2 else "")
        out.append(f"{label:>{axis_w - 1}} ", style="dim")
        out.append("│", style="dim")
        for ci, col in enumerate(cols):
            scaled = [v / peak * units for v in col]
            top = sum(scaled)
            cell_lo = (row - 1) * 8
            fill = min(max(top - cell_lo, 0), 8)
            if top > 0 and row == 1 and fill < 1:
                fill = 1  # make tiny non-zero values visible
            ch = _EIGHTHS[int(round(fill))] if fill < 8 else "█"
            # colour by the segment occupying the middle of this cell
            mid = cell_lo + min(fill, 8) / 2
            acc = 0.0
            color = colors[-1]
            for si, v in enumerate(scaled):
                acc += v
                if mid <= acc:
                    color = colors[si]
                    break
            bar_w = widths[ci] - gap
            out.append(ch * bar_w, style=color if ch.strip() else "")
            if gap:
                out.append(" ")
        out.append("\n")
    span = sum(widths)
    out.append(" " * axis_w + "└" + "─" * span, style="dim")
    if x_labels:
        out.append("\n")
        left, right = x_labels
        pad = max(span - len(left) - len(right), 1)
        out.append(" " * (axis_w + 1) + left + " " * pad + right, style="dim")
    return out


def hbar(value: float, total: float, width: int, color: str) -> Text:
    """A horizontal bar with eighth-block precision."""
    if total <= 0 or width <= 0:
        return Text(" " * max(width, 0))
    units = value / total * width * 8
    full = int(units // 8)
    rem = int(round(units - full * 8))
    text = Text("█" * full, style=color)
    if rem and full < width:
        text.append("▏▎▍▌▋▊▉"[min(rem, 7) - 1], style=color)
        full += 1
    text.append(" " * max(width - full, 0))
    return text


def proportion_bar(parts: list[tuple[float, str]], width: int) -> Text:
    """A single-line bar split into coloured parts proportional to their values."""
    total = sum(v for v, _ in parts)
    text = Text()
    if total <= 0:
        return Text("░" * width, style="dim")
    used = 0
    for i, (v, color) in enumerate(parts):
        n = round(v / total * width) if i < len(parts) - 1 else width - used
        n = max(n, 1 if v > 0 else 0)
        n = min(n, width - used)
        text.append("█" * n, style=color)
        used += n
    return text


HEAT_COLORS = ["grey23", "dark_green", "green4", "green3", "bright_green"]


def heatmap(per_day: dict[int, int], weeks: int, today: date | None = None) -> Text:
    """GitHub-style calendar: rows are weekdays, columns are weeks, ending today."""
    today = today or date.today()
    weeks = max(weeks, 4)
    # start on the Monday `weeks` weeks ago
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    values = [n for n in per_day.values() if n > 0]
    values.sort()

    def level(n: int) -> int:
        if n <= 0 or not values:
            return 0
        rank = sum(1 for v in values if v <= n) / len(values)
        return 1 + min(int(rank * 4 - 1e-9), 3)

    out = Text()
    # month labels
    month_row = [" "] * (weeks * 2)
    for w in range(weeks):
        d = start + timedelta(weeks=w)
        if d.day <= 7:
            label = d.strftime("%b")
            for i, ch in enumerate(label):
                if w * 2 + i < len(month_row):
                    month_row[w * 2 + i] = ch
    out.append("    " + "".join(month_row) + "\n", style="dim")
    names = ["Mon", "", "Wed", "", "Fri", "", "Sun"]
    for wd in range(7):
        out.append(f"{names[wd]:<4}", style="dim")
        for w in range(weeks):
            d = start + timedelta(weeks=w, days=wd)
            if d > today:
                out.append("  ")
                continue
            n = per_day.get((d - today).days, 0)
            out.append("■ ", style=HEAT_COLORS[level(n)])
        out.append("\n")
    out.append("    less ", style="dim")
    for c in HEAT_COLORS:
        out.append("■ ", style=c)
    out.append("more", style="dim")
    return out
