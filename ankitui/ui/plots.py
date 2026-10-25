"""Helpers for drawing statistics with textual-plot's PlotWidget."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from textual import events
from textual_hires_canvas import Canvas
from textual_plot import HiResMode, NumericAxisFormatter, PlotWidget
from textual_plot.axis_formatter import CategoricalAxisFormatter
from textual_plot.plot_widget import BarPlot


@dataclass
class StackedBarPlot(BarPlot):
    # cumulative tops of each layer, bottom layer first, with its colour
    layers: list[tuple[list[float], str]] = field(default_factory=list)


class StatsPlot(PlotWidget):
    """A plot that lets the mouse wheel / touchpad scroll the page around it.

    PlotWidget stops scroll events even with pan and zoom turned off, so they
    would never reach the scrollable stats page.
    """

    def stacked_bar(self, x: Sequence[float], layers: list[tuple[list[float], str]], width: float = 1.0) -> None:
        totals = layers[-1][0] if layers else [0.0] * len(x)
        x_values = np.array(x, dtype=float)
        self._datasets.append(
            StackedBarPlot(
                x=x_values,
                y=np.array(totals, dtype=float),
                width=np.full_like(x_values, width),
                bar_style="white",
                hires_mode=HiResMode.HALFBLOCK,
                layers=layers,
            )
        )
        self._labels.append(None)
        self._rerender()

    def _render_bar_plot(self, dataset: BarPlot) -> None:
        if isinstance(dataset, StackedBarPlot):
            self._render_stacked(dataset)
        else:
            super()._render_bar_plot(dataset)

    def _render_stacked(self, dataset: StackedBarPlot) -> None:
        """Draw stacked bars at half-cell resolution.

        Drawing the layers as separate half-block bars leaves a black half cell
        wherever two layers meet, because a cell has one glyph and the empty
        half is unstyled. Here each cell gets "▀" coloured with the upper half's
        layer as foreground and the lower half's layer as background.
        """
        canvas = self.query_one("#plot", Canvas)
        for i, (xi, width) in enumerate(zip(dataset.x, dataset.width)):
            left, _ = self.get_hires_pixel_from_coordinate(xi - width / 2, 0)
            right, _ = self.get_hires_pixel_from_coordinate(xi + width / 2, 0)
            col0, col1 = int(round(left)), int(round(right))
            if col1 <= col0:
                col1 = col0 + 1
            halves: dict[int, str] = {}
            bottom = 0.0
            for tops, color in dataset.layers:
                top = float(tops[i])
                if top > bottom:
                    _, y_low = self.get_hires_pixel_from_coordinate(xi, bottom)
                    _, y_high = self.get_hires_pixel_from_coordinate(xi, top)
                    for half in range(int(round(y_high * 2)), int(round(y_low * 2))):
                        halves[half] = color
                bottom = max(bottom, top)
            for row in {h // 2 for h in halves}:
                upper, lower = halves.get(2 * row), halves.get(2 * row + 1)
                if upper and lower:
                    char, style = ("█", upper) if upper == lower else ("▀", f"{upper} on {lower}")
                elif upper:
                    char, style = "▀", upper
                else:
                    char, style = "▄", lower
                for col in range(col0, col1):
                    canvas.set_pixel(col, row, char, style)

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
    plot: StatsPlot,
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
    plot.stacked_bar(list(x), layers)
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
