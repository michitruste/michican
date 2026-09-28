"""Live signal plot (pyqtgraph): overlay or stacked view, rolling time window, crosshair readout."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from ..signal_store import SignalStore

# Categorical palette (dark-surface steps), assigned in fixed order.
PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500",
           "#d55181", "#008300", "#9085e9", "#e66767"]
SURFACE = "#1a1a19"
TEXT = "#c3c2b7"
MUTED = "#8a8980"

pg.setConfigOptions(background=SURFACE, foreground=TEXT, antialias=True)


@dataclass
class Trace:
    key: str
    unit: str
    slot: int
    curve: pg.PlotDataItem | None = None
    plot: pg.PlotItem | None = None

    @property
    def color(self) -> str:
        return PALETTE[self.slot % len(PALETTE)]

    @property
    def pen(self):
        # Past 8 series the hues repeat, so add a dash pattern as a second encoding.
        style = QtCore.Qt.PenStyle.SolidLine if self.slot < len(PALETTE) else QtCore.Qt.PenStyle.DashLine
        return pg.mkPen(self.color, width=2, style=style)

    @property
    def label(self) -> str:
        return f"{self.key} [{self.unit}]" if self.unit else self.key


class PlotPanel(QtWidgets.QWidget):
    plotted_changed = QtCore.Signal(set)   # keys currently plotted

    def __init__(self, store: SignalStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.traces: dict[str, Trace] = {}
        self._plots: list[pg.PlotItem] = []
        self._vlines: list[pg.InfiniteLine] = []
        self._t_now = 0.0

        # --- controls
        self.window_spin = QtWidgets.QDoubleSpinBox(suffix=" s", decimals=1, minimum=0,
                                                    maximum=3600, value=20, singleStep=5)
        self.window_spin.setToolTip("Visible time window (0 = whole history)")
        self.pause_btn = QtWidgets.QPushButton("Pause", checkable=True)
        self.pause_btn.setToolTip("Freeze the plot to zoom/pan with the mouse")
        self.stacked_chk = QtWidgets.QCheckBox("Stacked")
        self.stacked_chk.setToolTip("One plot per signal with its own Y axis")
        clear_btn = QtWidgets.QPushButton("Remove all")

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Window"))
        bar.addWidget(self.window_spin)
        bar.addWidget(self.pause_btn)
        bar.addWidget(self.stacked_chk)
        bar.addStretch(1)
        bar.addWidget(clear_btn)

        self.glw = pg.GraphicsLayoutWidget()
        self.readout = QtWidgets.QLabel(" ")
        self.readout.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.readout.setWordWrap(True)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.glw, 1)
        lay.addWidget(self.readout)

        self.pause_btn.toggled.connect(self._on_pause)
        self.stacked_chk.toggled.connect(lambda _: self._rebuild())
        clear_btn.clicked.connect(self.clear)
        self._mouse_proxy = pg.SignalProxy(self.glw.scene().sigMouseMoved,
                                           rateLimit=30, slot=self._on_mouse)
        self._rebuild()

    # ------------------------------------------------------------ public API
    def add_signal(self, key: str, unit: str = "") -> None:
        if key in self.traces:
            return
        used = {t.slot for t in self.traces.values()}
        slot = next(i for i in range(len(used) + 1) if i not in used)
        self.traces[key] = Trace(key, unit, slot)
        self._rebuild()
        self.plotted_changed.emit(set(self.traces))

    def remove_signal(self, key: str) -> None:
        if self.traces.pop(key, None) is not None:
            self._rebuild()
            self.plotted_changed.emit(set(self.traces))

    def clear(self) -> None:
        self.traces.clear()
        self._rebuild()
        self.plotted_changed.emit(set())

    def plotted(self) -> list[str]:
        return list(self.traces)

    def refresh(self, t_now: float) -> None:
        """Pull fresh data from the store. Called by the GUI timer."""
        self._t_now = t_now
        if self.pause_btn.isChecked() or not self.traces:
            return
        window = self.window_spin.value()
        t_start = t_now - window if window > 0 else None
        for tr in self.traces.values():
            x, y = self.store.window(tr.key, None if t_start is None else t_start - 1.0)
            tr.curve.setData(x, y)
        first = self._plots[0]
        if t_start is None:
            first.enableAutoRange(x=True)
        else:
            # until one window of data exists, show [0, window] instead of negative time
            first.setXRange(max(t_start, 0.0), max(t_now, window), padding=0)

    # ------------------------------------------------------------ internals
    def _new_plot(self, row: int) -> pg.PlotItem:
        p = self.glw.addPlot(row=row, col=0)
        p.showGrid(x=True, y=True, alpha=0.15)
        p.setAutoVisible(y=True)
        p.setMenuEnabled(True)
        p.getAxis("bottom").setLabel("time", units="s")
        vline = pg.InfiniteLine(angle=90, movable=False,
                                pen=pg.mkPen(MUTED, width=1, style=QtCore.Qt.PenStyle.DashLine))
        vline.setVisible(False)
        p.addItem(vline, ignoreBounds=True)
        self._plots.append(p)
        self._vlines.append(vline)
        return p

    def _make_curve(self, plot: pg.PlotItem, tr: Trace) -> None:
        tr.plot = plot
        tr.curve = plot.plot(name=tr.label, pen=tr.pen)
        tr.curve.setDownsampling(auto=True, method="peak")
        tr.curve.setClipToView(True)

    def _rebuild(self) -> None:
        self.glw.clear()
        self._plots.clear()
        self._vlines.clear()
        traces = sorted(self.traces.values(), key=lambda t: t.slot)
        if self.stacked_chk.isChecked() and traces:
            for i, tr in enumerate(traces):
                p = self._new_plot(i)
                p.setTitle(tr.label, size="9pt", color=TEXT)
                if i > 0:
                    p.setXLink(self._plots[0])
                if i < len(traces) - 1:
                    p.getAxis("bottom").setStyle(showValues=False)
                    p.getAxis("bottom").setLabel("")
                self._make_curve(p, tr)
        else:
            p = self._new_plot(0)
            if len(traces) >= 2:
                p.addLegend(offset=(10, 10), labelTextColor=TEXT,
                            brush=pg.mkBrush(26, 26, 25, 210), pen=pg.mkPen(MUTED))
            elif traces:
                p.setTitle(traces[0].label, size="9pt", color=TEXT)
            for tr in traces:
                self._make_curve(p, tr)
        self.refresh(self._t_now)
        if self.pause_btn.isChecked():
            # Show the frozen data immediately even though refresh() skips while paused.
            self._fill_all()

    def _fill_all(self) -> None:
        for tr in self.traces.values():
            tr.curve.setData(*self.store.window(tr.key, None))

    def _on_pause(self, paused: bool) -> None:
        self.pause_btn.setText("Resume" if paused else "Pause")
        if paused:
            self._fill_all()  # full history so the user can pan back in time

    def _on_mouse(self, evt) -> None:
        pos = evt[0]
        plot = next((p for p in self._plots if p.sceneBoundingRect().contains(pos)), None)
        if plot is None or not self.traces:
            for v in self._vlines:
                v.setVisible(False)
            return
        t = plot.vb.mapSceneToView(pos).x()
        for v in self._vlines:
            v.setPos(t)
            v.setVisible(True)
        parts = [f"<span style='color:{TEXT}'>t = {t:.3f} s</span>"]
        for tr in sorted(self.traces.values(), key=lambda t: t.slot):
            x, y = tr.curve.getOriginalDataset()
            if x is None or len(x) == 0:
                continue
            i = int(np.searchsorted(x, t, side="right")) - 1
            if i < 0:
                continue
            parts.append(f"<span style='color:{tr.color}'>&#9632;</span> "
                         f"{tr.key} = <b>{y[i]:.6g}</b> {tr.unit}")
        self.readout.setText(" &nbsp;&nbsp; ".join(parts))
