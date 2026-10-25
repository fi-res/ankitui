"""Helpers for drawing statistics with textual-plot's PlotWidget."""

from __future__ import annotations

from typing import Sequence

from textual import events
from textual_plot import HiResMode, NumericAxisFormatter, PlotWidget
from textual_plot.axis_formatter import CategoricalAxisFormatter


class StatsPlot(PlotWidget):
    """A plot that lets the mouse wheel / touchpad scroll the page around it.

    PlotWidget stops scroll events even with pan and zoom turned off, so they
    would never reach the scrollable stats page.
    """

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._scroll_page(event, down=True)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._scroll_page(event, down=False)

    def _scroll_page(self, event: events.MouseEvent, down: bool) -> None:
        # skips PlotWidget's zoom handlers, which run after this class's
        event.prevent_default()
        event.stop()
        for widget in self.ancestors_with_self[1:]:
            if getattr(widget, "allow_vertical_scroll", False):
                if down:
                    widget._scroll_down_for_pointer(animate=False)
                else:
                    widget._scroll_up_for_pointer(animate=False)
                return


def new_plot(ident: str) -> PlotWidget:
    return StatsPlot(id=ident, classes="stats-plot", allow_pan_and_zoom=False)


def stacked_bars(
    plot: PlotWidget,
    x: Sequence[float],
    series: Sequence[tuple[Sequence[float], str]],
    x_limits: tuple[float, float] | None = None,
) -> None:
    """Draw stacked bars: series are listed bottom first, as (values, colour)."""
    plot.clear()
    plot.set_x_formatter(NumericAxisFormatter())
    plot.set_xticks(None)
    n = len(x)
    cumulative = [0.0] * n
    layers = []
    for values, color in series:
        cumulative = [c + float(v) for c, v in zip(cumulative, values)]
        layers.append((list(cumulative), color))
    # textual-plot has no stacking, so the tallest layer is drawn first and
    # every lower layer is painted over it
    for heights, color in reversed(layers):
        if any(heights):
            plot.bar(list(x), heights, width=1.0, bar_style=color, hires_mode=HiResMode.HALFBLOCK)
    top = max(cumulative) if cumulative else 0
    plot.set_ylimits(0, top * 1.05 if top else 1)
    if x_limits is not None:
        plot.set_xlimits(*x_limits)
    elif n:
        plot.set_xlimits(min(x) - 0.5, max(x) + 0.5)


def category_bars(plot: PlotWidget, labels: Sequence[str], values: Sequence[float], color: str) -> None:
    plot.clear()
    positions = list(range(1, len(labels) + 1))
    plot.set_x_formatter(CategoricalAxisFormatter(list(labels)))
    plot.set_xticks(positions)
    plot.bar(positions, list(values), width=0.8, bar_style=color, hires_mode=HiResMode.HALFBLOCK)
    top = max(values) if values else 0
    plot.set_ylimits(0, top * 1.1 if top else 1)
    plot.set_xlimits(0.4, len(labels) + 0.6)
