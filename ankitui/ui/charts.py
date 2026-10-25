"""Terminal charts built from Unicode block characters."""

from __future__ import annotations

from datetime import date, timedelta

from rich.text import Text

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
