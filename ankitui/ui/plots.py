"""Helpers for drawing statistics with textual-plot's PlotWidget."""

from __future__ import annotations

from typing import Sequence

from textual_plot import HiResMode, NumericAxisFormatter, PlotWidget
from textual_plot.axis_formatter import CategoricalAxisFormatter


def new_plot(ident: str) -> PlotWidget:
    # pan/zoom would capture the mouse wheel and break scrolling of the stats page
    return PlotWidget(id=ident, classes="stats-plot", allow_pan_and_zoom=False)


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


def add_line(plot: PlotWidget, x: Sequence[float], y: Sequence[float], color: str) -> None:
    plot.plot(list(x), list(y), line_style=color, hires_mode=HiResMode.BRAILLE)


def moving_average(values: Sequence[float], window: int) -> list[float]:
    out = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(total / min(i + 1, window))
    return out


def category_bars(plot: PlotWidget, labels: Sequence[str], values: Sequence[float], color: str) -> None:
    plot.clear()
    positions = list(range(1, len(labels) + 1))
    plot.set_x_formatter(CategoricalAxisFormatter(list(labels)))
    plot.set_xticks(positions)
    plot.bar(positions, list(values), width=0.8, bar_style=color, hires_mode=HiResMode.HALFBLOCK)
    top = max(values) if values else 0
    plot.set_ylimits(0, top * 1.1 if top else 1)
    plot.set_xlimits(0.4, len(labels) + 0.6)
