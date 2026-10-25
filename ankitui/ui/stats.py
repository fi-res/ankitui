"""Statistics screen (like the Statistics page of Anki / AnkiDroid)."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Select, Static
from textual_plot import PlotWidget

from ..core.stats import REVIEW_KINDS, Stats
from .charts import heatmap, hbar, proportion_bar
from .icons import icon
from .plots import category_bars, new_plot, stacked_bars

KIND_COLORS = {
    "learn": "dark_orange",
    "relearn": "red3",
    "young": "#a3e635",
    "mature": "#16a34a",
    "filtered": "steel_blue1",
}
KIND_LABELS = {"learn": "Learning", "relearn": "Relearning", "young": "Young", "mature": "Mature", "filtered": "Filtered"}
COUNT_COLORS = {
    "new": "dodger_blue1",
    "learning": "dark_orange",
    "relearning": "red3",
    "young": "#a3e635",
    "mature": "#16a34a",
    "suspended": "yellow3",
    "buried": "grey50",
}
BUTTON_COLORS = ["red3", "dark_orange", "green3", "dodger_blue1"]

# short tick labels for the histogram buckets in core.stats (upper bound of each bucket)
INTERVAL_TICKS = ["1d", "2d", "4d", "1w", "2w", "1m", "2m", "4m", "6m", "1y", "2y", "2y+"]
EASE_TICKS = ["149", "179", "209", "239", "259", "279", "309", "310+"]

PERIODS = [("1 month", 30), ("3 months", 90), ("1 year", 365), ("Deck life", 0)]


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _minutes(ms: int) -> str:
    mins = ms / 60000
    if mins >= 120:
        return f"{mins / 60:.1f} hours"
    return f"{mins:.1f} minutes"


def _legend(items: list[tuple[str, str]]) -> Text:
    text = Text()
    for label, color in items:
        text.append("■ ", style=color)
        text.append(label + "   ", style="dim")
    return text


PLOT_PANELS = {"future", "reviews", "time", "intervals", "ease", "hourly", "added"}


class StatsPanel(Vertical):
    """A bordered panel with an optional plot and a text body rebuilt for its width."""

    def __init__(self, key: str, title: str, full: bool = False):
        super().__init__(id=f"panel-{key}", classes="stats-panel" + (" full" if full else ""))
        self.key = key
        self.border_title = title

    def compose(self) -> ComposeResult:
        if self.key in PLOT_PANELS:
            yield new_plot(f"plot-{self.key}")
        yield Static("", classes="stats-body")

    def on_mount(self) -> None:
        if self.key in PLOT_PANELS:
            self.plot.margin_top = 1
            self.plot.margin_left = 7

    @property
    def plot(self):
        return self.query_one(PlotWidget)

    @property
    def body(self) -> Static:
        return self.query_one(".stats-body", Static)


class StatsScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("d", "toggle_scope", "Deck/Collection"),
        Binding("p", "next_period", "Period"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, did: int | None = None):
        super().__init__()
        self.did = did
        self.whole_collection = did is None
        self.period = 30
        self._resize_timer = None

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        yield Header()
        deck_name = self.col.get_deck(self.did)["name"] if self.did else None
        scope_options = [("Whole collection", "col")]
        if deck_name:
            scope_options.insert(0, (f"Deck: {deck_name}", "deck"))
        with Horizontal(id="stats-toolbar"):
            yield Label(icon("stats") + "Show")
            yield Select(scope_options, allow_blank=False, value="col" if self.whole_collection else "deck", id="scope")
            yield Label(icon("calendar") + "Period")
            yield Select(PERIODS, allow_blank=False, value=self.period, id="period")
        with VerticalScroll(id="stats-scroll"):
            with Vertical(id="stats-grid"):
                with Horizontal(classes="stats-pair"):
                    yield StatsPanel("today", icon("sun") + "Today")
                    yield StatsPanel("counts", icon("pie") + "Card Counts")
                yield StatsPanel("future", icon("line") + "Future Due", full=True)
                yield StatsPanel("calendar", icon("calendar") + "Calendar", full=True)
                yield StatsPanel("reviews", icon("review") + "Reviews", full=True)
                yield StatsPanel("time", icon("hourglass") + "Review Time", full=True)
                with Horizontal(classes="stats-pair"):
                    yield StatsPanel("intervals", icon("clock") + "Review Intervals")
                    yield StatsPanel("ease", icon("target") + "Card Ease")
                with Horizontal(classes="stats-pair"):
                    yield StatsPanel("hourly", icon("sun") + "Hourly Breakdown")
                    yield StatsPanel("buttons", icon("keyboard") + "Answer Buttons")
                yield StatsPanel("added", icon("add") + "Added", full=True)
                yield StatsPanel("retention", icon("check") + "True Retention", full=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Statistics"
        self.call_after_refresh(self.refresh_stats)

    def on_resize(self) -> None:
        self.query_one("#stats-grid").set_class(self.size.width < 110, "narrow")
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(0.15, self.refresh_stats)

    @on(Select.Changed, "#scope")
    def scope_changed(self, event: Select.Changed) -> None:
        self.whole_collection = event.value == "col"
        self.refresh_stats()

    @on(Select.Changed, "#period")
    def period_changed(self, event: Select.Changed) -> None:
        self.period = int(event.value)
        self.refresh_stats()

    def action_toggle_scope(self) -> None:
        if self.did is None:
            return
        sel = self.query_one("#scope", Select)
        sel.value = "deck" if sel.value == "col" else "col"

    def action_next_period(self) -> None:
        values = [v for _, v in PERIODS]
        sel = self.query_one("#period", Select)
        sel.value = values[(values.index(sel.value) + 1) % len(values)]

    def action_refresh(self) -> None:
        self.refresh_stats()

    # ------------------------------------------------------------ rendering

    def _days(self, stats: Stats) -> int:
        if self.period:
            return self.period
        first = stats.first_review_day()
        return max(min(-first + 1 if first is not None else 30, 3650), 7)

    def refresh_stats(self) -> None:
        stats = Stats(self.col, None if self.whole_collection else self.did)
        days = self._days(stats)
        builders = {
            "today": lambda p, w: self.render_today(stats),
            "counts": lambda p, w: self.render_counts(stats, w),
            "future": lambda p, w: self.render_future(stats, p, days),
            "calendar": lambda p, w: self.render_calendar(stats, w),
            "reviews": lambda p, w: self.render_reviews(stats, p, days, by_time=False),
            "time": lambda p, w: self.render_reviews(stats, p, days, by_time=True),
            "intervals": lambda p, w: self.render_histogram(
                stats.intervals(), p, "#a3e635", "Average interval", "d", INTERVAL_TICKS
            ),
            "ease": lambda p, w: self.render_histogram(stats.eases(), p, "dodger_blue1", "Average ease", "%", EASE_TICKS),
            "hourly": lambda p, w: self.render_hourly(stats, p, days),
            "buttons": lambda p, w: self.render_buttons(stats, w, days),
            "added": lambda p, w: self.render_added(stats, p, days),
            "retention": lambda p, w: self.render_retention(stats),
        }
        for panel in self.query(StatsPanel):
            narrow = self.size.width < 110
            width = panel.content_size.width or max(self.size.width // (1 if narrow or "full" in panel.classes else 2) - 6, 30)
            panel.body.update(builders[panel.key](panel, max(width, 20)))

    def render_today(self, stats: Stats):
        t = stats.today_stats()
        if not t.cards:
            return Text("No cards have been studied today.", style="dim")
        text = Text()
        text.append(f"Studied {t.cards} card{'s' if t.cards != 1 else ''} in {_minutes(t.time_ms)} today ", style="bold")
        text.append(f"({t.time_ms / 1000 / t.cards:.1f}s/card)\n", style="dim")
        text.append(f"Again count: {t.again}  ")
        text.append(f"({_pct(t.correct_pct)} correct)\n", style="dim")
        text.append(f"Learn: {t.learn}   Review: {t.review}   Relearn: {t.relearn}   Filtered: {t.filtered}\n")
        if t.mature_count:
            text.append(
                f"Correct answers on mature cards: {t.mature_correct}/{t.mature_count} "
                f"({100 * t.mature_correct / t.mature_count:.1f}%)"
            )
        else:
            text.append("No mature cards were studied today.", style="dim")
        return text

    def render_counts(self, stats: Stats, width: int):
        c = stats.card_counts()
        parts = [
            ("new", c.new), ("learning", c.learning), ("relearning", c.relearning), ("young", c.young),
            ("mature", c.mature), ("suspended", c.suspended), ("buried", c.buried),
        ]
        total = c.total
        if not total:
            return Text("No cards.", style="dim")
        table = Table.grid(padding=(0, 1))
        table.add_column()
        table.add_column(justify="right")
        table.add_column(justify="right", style="dim")
        bar_w = max(width - 30, 6)
        table.add_column()
        for name, n in parts:
            table.add_row(
                Text("■ ", style=COUNT_COLORS[name]) + Text(name.capitalize()),
                str(n),
                f"{100 * n / total:.1f}%",
                hbar(n, total, bar_w, COUNT_COLORS[name]),
            )
        return Group(proportion_bar([(n, COUNT_COLORS[k]) for k, n in parts], width), Text(""), table,
                     Text(f"Total: {total} cards", style="bold"))

    def render_future(self, stats: Stats, panel: StatsPanel, days: int):
        days = min(max(days, 7), 365)
        f = stats.future_due(days)
        stacked_bars(panel.plot, list(range(days)), [(f.days, "green3")])
        panel.plot.set_xlabel("days from today")
        avg = sum(f.days) / days
        info = Text()
        info.append(f"Total: {f.total}   Due tomorrow: {f.tomorrow}   Average: {avg:.1f} cards/day")
        if f.overdue:
            info.append(f"   Overdue: {f.overdue}", style="red")
        return info

    def render_calendar(self, stats: Stats, width: int):
        weeks = max(min((width - 6) // 2, 53), 4)
        per_day = stats.calendar(weeks * 7).per_day
        cal = stats.calendar(365)
        info = Text()
        info.append(f"\n{icon('fire')}Current streak: ", style="dim")
        info.append(f"{cal.current_streak} day{'s' if cal.current_streak != 1 else ''}", style="bold orange1")
        info.append(f"   {icon('trophy')}Longest streak: ", style="dim")
        info.append(f"{cal.longest_streak}", style="bold")
        info.append(
            f"   Days studied in the last year: {cal.days_studied} "
            f"({100 * cal.days_studied / cal.period_days:.0f}%)   "
            f"Average: {cal.average_on_study_days:.1f}/study day",
            style="dim",
        )
        return Group(heatmap(per_day, weeks), info)

    def render_reviews(self, stats: Stats, panel: StatsPanel, days: int, by_time: bool):
        hist = stats.review_history(days)
        source = hist.times if by_time else hist.counts
        scale = 60000 if by_time else 1
        x = list(range(-days + 1, 1))
        series = [([d[k] / scale for d in source], KIND_COLORS[k]) for k in REVIEW_KINDS]
        stacked_bars(panel.plot, x, series)
        totals = [sum(d.values()) / scale for d in source]
        panel.plot.set_xlabel("days ago")
        panel.plot.set_ylabel("minutes" if by_time else "reviews")
        studied = hist.days_studied()
        info = Text()
        if by_time:
            total_min = hist.total_time_ms() / 60000
            info.append(f"Total: {_minutes(hist.total_time_ms())}   ")
            if studied:
                info.append(f"Average: {total_min / studied:.1f} min/study day   ")
            reviews = hist.total()
            if reviews:
                info.append(f"{hist.total_time_ms() / 1000 / reviews:.1f}s per card", style="dim")
        else:
            total = hist.total()
            info.append(f"Total: {total} reviews   Days studied: {studied}/{days}   ")
            if studied:
                info.append(f"Average: {total / studied:.1f}/study day ({total / days:.1f} overall)", style="dim")
        if days >= 14:
            week = sum(totals[-7:]) / 7
            info.append(f"\nLast 7 days: {week:.1f} {'min' if by_time else 'reviews'}/day", style="dim")
        items = [(KIND_LABELS[k], KIND_COLORS[k]) for k in REVIEW_KINDS]
        return Group(info, _legend(items))

    def render_histogram(self, h, panel: StatsPanel, color: str, avg_label: str, unit: str, ticks: list[str]):
        category_bars(panel.plot, ticks, [n for _, n in h.buckets], color)
        if not h.total:
            return Text("No review cards yet.", style="dim")
        return Text(f"{avg_label}: {h.average:.0f}{unit}   Median: {h.median}{unit}   Cards: {h.total}", style="bold")

    def render_hourly(self, stats: Stats, panel: StatsPanel, days: int):
        hours = stats.hourly(days)
        stacked_bars(panel.plot, list(range(24)), [([c for _, c in hours], "green3"), ([n - c for n, c in hours], "red3")])
        panel.plot.set_xlabel("hour")
        if not any(n for n, _ in hours):
            return Text("No reviews in this period.", style="dim")
        best = max(range(24), key=lambda h: (hours[h][1] / hours[h][0]) if hours[h][0] >= 5 else -1)
        info = Text()
        if hours[best][0] >= 5:
            info.append(f"Best hour: {best}:00 ({100 * hours[best][1] / hours[best][0]:.0f}% correct)   ", style="dim")
        return Group(info, _legend([("Correct", "green3"), ("Again", "red3")]))

    def render_buttons(self, stats: Stats, width: int, days: int):
        b = stats.buttons(days)
        if not any(sum(v) for v in b.counts.values()):
            return Text("No reviews in this period.", style="dim")
        out = Text()
        bar_w = max(width - 2, 10)
        for kind in ("learning", "young", "mature"):
            row = b.counts[kind]
            total = sum(row)
            out.append(f"{kind.capitalize():<9}", style="bold")
            out.append(f" {total} answers, {_pct(b.correct_pct(kind))} correct\n", style="dim")
            out.append_text(proportion_bar([(n, BUTTON_COLORS[i]) for i, n in enumerate(row)], bar_w) if total else Text("─" * bar_w, style="dim"))
            out.append("\n")
            out.append("  ".join(f"{name} {n}" for name, n in zip(("Again", "Hard", "Good", "Easy"), row)) + "\n\n")
        out.append_text(_legend(list(zip(("Again", "Hard", "Good", "Easy"), BUTTON_COLORS))))
        return out

    def render_added(self, stats: Stats, panel: StatsPanel, days: int):
        added = stats.added(days)
        total = sum(added)
        stacked_bars(panel.plot, list(range(-days + 1, 1)), [(added, "dodger_blue1")])
        panel.plot.set_xlabel("days ago")
        return Text(f"Total: {total} cards   Average: {total / days:.1f}/day")

    def render_retention(self, stats: Stats):
        table = Table(box=None, padding=(0, 2), header_style="bold")
        table.add_column("")
        table.add_column("Young", justify="right")
        table.add_column("Mature", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Reviews", justify="right", style="dim")
        for row in stats.true_retention():
            def cell(v):
                if v is None:
                    return Text("—", style="dim")
                color = "green3" if v >= 90 else ("yellow3" if v >= 80 else "red3")
                return Text(f"{v:.1f}%", style=color)

            table.add_row(row.label, cell(row.young), cell(row.mature), cell(row.total), str(row.count))
        note = Text("Pass rate of review cards (Hard/Good/Easy count as passed). Mature: interval ≥ 21 days.", style="dim")
        return Group(table, note)
