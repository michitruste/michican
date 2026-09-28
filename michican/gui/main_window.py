"""Main application window: wires the CAN manager, DBC, trace, plot and transmit panels together."""
from __future__ import annotations

import os
import queue
import time

import can
from PySide6 import QtCore, QtGui, QtWidgets

from ..bus import CanManager
from ..dbc import DbcManager
from ..signal_store import SignalStore
from ..simulator import DemoTraffic
from .connection_bar import ConnectionBar
from .plot_panel import PlotPanel
from .signal_tree import SignalTree
from .trace_view import TraceView
from .transmit_panel import TransmitPanel

DBC_FILTER = "CAN databases (*.dbc *.arxml *.kcd *.sym);;All files (*)"
LOG_FILTER = "CAN logs (*.blf *.asc *.log *.csv *.trc *.mf4);;All files (*)"
TICK_MS = 50
MAX_ITEMS_PER_TICK = 50_000


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, dbc_paths: list[str] | None = None) -> None:
        super().__init__()
        self.setWindowTitle("MichiCAN – CAN monitor / sender / plotter")
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.settings = QtCore.QSettings("michican", "michican")

        self.dbc = DbcManager()
        self.store = SignalStore()
        self.manager = CanManager(self.dbc, self.store)
        self.demo: DemoTraffic | None = None
        self._t_last = 0.0
        self._msg_counts: dict[str, int] = {}
        self._rate_prev = (time.monotonic(), 0)

        # ---------------- widgets
        self.conn = ConnectionBar()
        tb = self.addToolBar("Connection")
        tb.setObjectName("connection_toolbar")
        tb.setMovable(False)
        tb.addWidget(self.conn)

        self.tree = SignalTree(self.dbc, self.store)
        self.plot = PlotPanel(self.store)
        self.trace = TraceView()
        self.transmit = TransmitPanel(self.dbc, self.manager)

        self.top_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.top_split.addWidget(self.tree)
        self.top_split.addWidget(self.plot)
        self.top_split.setStretchFactor(1, 3)
        self.top_split.setSizes([380, 1000])
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.trace, "Trace")
        self.tabs.addTab(self.transmit, "Transmit")
        self.main_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.main_split.addWidget(self.top_split)
        self.main_split.addWidget(self.tabs)
        self.main_split.setSizes([560, 340])
        self.setCentralWidget(self.main_split)

        # ---------------- status bar
        self.state_lbl = QtWidgets.QLabel("Disconnected")
        self.count_lbl = QtWidgets.QLabel()
        self.dbc_lbl = QtWidgets.QLabel("No DBC loaded")
        self.rec_lbl = QtWidgets.QLabel()
        sb = self.statusBar()
        for w in (self.state_lbl, self.count_lbl, self.rec_lbl):
            sb.addPermanentWidget(w)
        sb.addPermanentWidget(self.dbc_lbl)

        self._build_menus()

        # ---------------- wiring
        self.conn.connect_toggled.connect(self._on_connect_toggled)
        self.tree.plot_toggled.connect(self._on_plot_toggled)
        self.tree.message_activated.connect(self._open_in_transmit)
        self.plot.plotted_changed.connect(self.tree.set_plotted)
        self.transmit.status.connect(lambda s: self.statusBar().showMessage(s, 5000))

        self.timer = QtCore.QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self._restore_settings()
        for p in dbc_paths or []:
            self.load_dbc(p)

    # ------------------------------------------------------------ menus
    def _build_menus(self) -> None:
        m = self.menuBar().addMenu("&File")
        m.addAction("Load &DBC…", QtGui.QKeySequence.StandardKey.Open, self._ask_load_dbc)
        m.addAction("Unload all DBCs", self._clear_dbc)
        m.addSeparator()
        self.rec_action = m.addAction("Start &recording…")
        self.rec_action.setCheckable(True)
        self.rec_action.toggled.connect(self._toggle_recording)
        m.addAction("Open &log file (offline)…", self._open_log)
        m.addAction("&Export signals to CSV…", self._export_csv)
        m.addSeparator()
        m.addAction("&Quit", QtGui.QKeySequence.StandardKey.Quit, self.close)

        m = self.menuBar().addMenu("&View")
        m.addAction("Clear trace && plot data", self._reset_data)

        m = self.menuBar().addMenu("&Tools")
        self.demo_action = m.addAction("Demo traffic (virtual bus)")
        self.demo_action.setCheckable(True)
        self.demo_action.toggled.connect(self._toggle_demo)
        m.addAction("Vector Hardware Config…", self._vector_hw_config)

        m = self.menuBar().addMenu("&Help")
        m.addAction("About", self._about)

    # ------------------------------------------------------------ DBC
    def load_dbc(self, path: str) -> None:
        if os.path.abspath(path) in self.dbc.files:
            return
        try:
            n = self.dbc.load(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Load DBC", f"Could not load {path}:\n{exc}")
            return
        self.statusBar().showMessage(f"Loaded {n} messages from {os.path.basename(path)}", 5000)
        self._on_dbc_changed()

    def _ask_load_dbc(self) -> None:
        start = self.settings.value("last_dir", "")
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Load DBC", start, DBC_FILTER)
        for p in paths:
            self.settings.setValue("last_dir", os.path.dirname(p))
            self.load_dbc(p)

    def _clear_dbc(self) -> None:
        self.plot.clear()
        self.dbc.clear()
        self._on_dbc_changed()

    def _on_dbc_changed(self) -> None:
        self.tree.rebuild(set(self.plot.plotted()))
        self.transmit.rebuild()
        files = [os.path.basename(f) for f in self.dbc.files]
        self.dbc_lbl.setText("DBC: " + ", ".join(files) if files else "No DBC loaded")
        self.dbc_lbl.setToolTip("\n".join(self.dbc.files))

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".dbc", ".arxml", ".kcd", ".sym")):
                self.load_dbc(path)

    # ------------------------------------------------------------ connection
    def _on_connect_toggled(self, on: bool) -> None:
        if not on:
            self._disconnect()
            return
        cfg = self.conn.config()
        self._reset_data()
        try:
            self.manager.connect(cfg)
        except Exception as exc:
            self.conn.set_connected(False)
            QtWidgets.QMessageBox.critical(self, "Connect", f"Could not open {cfg.interface} "
                                                            f"channel {cfg.channel}:\n\n{exc}")
            return
        self.conn.set_connected(True)
        self.transmit.set_connected(True)
        self.state_lbl.setText(f"Connected: {self.manager.bus.channel_info}")
        self._save_settings()

    def _disconnect(self) -> None:
        self.demo_action.setChecked(False)
        self.rec_action.setChecked(False)
        self.manager.disconnect()
        self.conn.set_connected(False)
        self.transmit.set_connected(False)
        self.state_lbl.setText("Disconnected")

    def _reset_data(self) -> None:
        while True:
            try:
                self.manager.rx_queue.get_nowait()
            except queue.Empty:
                break
        self.store.clear()
        self.manager.reset_time()
        self.trace.clear()
        self._t_last = 0.0
        self._msg_counts.clear()

    # ------------------------------------------------------------ periodic GUI update
    def _tick(self) -> None:
        items = []
        q = self.manager.rx_queue
        try:
            while len(items) < MAX_ITEMS_PER_TICK:
                items.append(q.get_nowait())
        except queue.Empty:
            pass

        if items:
            self._t_last = max(self._t_last, items[-1].t)
            names = set()
            for it in items:
                if it.decoded is not None:
                    n = it.decoded.message.name
                    names.add(n)
                    self._msg_counts[n] = self._msg_counts.get(n, 0) + 1
            self.trace.add_batch(items)
            self.tree.update_values(names, self._msg_counts)

        try:
            while True:
                self.statusBar().showMessage(self.manager.events.get_nowait(), 5000)
        except queue.Empty:
            pass

        t_now = self._t_last
        if self.manager.connected and self.manager.t0 is not None:
            # keep the plot scrolling even when the bus goes quiet
            t_now = max(t_now, time.time() - self.manager.t0)
        self.plot.refresh(t_now)
        self._update_counters()

    def _update_counters(self) -> None:
        now = time.monotonic()
        t_prev, n_prev = self._rate_prev
        if now - t_prev < 1.0:
            return
        m = self.manager
        total = m.rx_count + m.tx_count
        rate = (total - n_prev) / (now - t_prev)
        self._rate_prev = (now, total)
        self.count_lbl.setText(f"Rx {m.rx_count}  Tx {m.tx_count}  Err {m.err_count}  "
                               f"{rate:,.0f} frames/s")

    # ------------------------------------------------------------ plot / transmit hooks
    def _on_plot_toggled(self, key: str, unit: str, checked: bool) -> None:
        if checked:
            self.plot.add_signal(key, unit)
        else:
            self.plot.remove_signal(key)

    def _open_in_transmit(self, message_name: str) -> None:
        self.transmit.select_message(message_name)
        self.tabs.setCurrentWidget(self.transmit)

    # ------------------------------------------------------------ recording / logs / export
    def _toggle_recording(self, on: bool) -> None:
        if not on:
            if self.manager.logging_active:
                self.manager.stop_logging()
                self.statusBar().showMessage("Recording stopped", 5000)
            self.rec_lbl.clear()
            self.rec_action.setText("Start &recording…")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Record to", self.settings.value("last_dir", ""),
            "Vector BLF (*.blf);;Vector ASC (*.asc);;CSV (*.csv);;Peak TRC (*.trc)")
        if not path:
            self.rec_action.blockSignals(True)
            self.rec_action.setChecked(False)
            self.rec_action.blockSignals(False)
            return
        try:
            self.manager.start_logging(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Record", str(exc))
            self.rec_action.setChecked(False)
            return
        self.rec_lbl.setText(f"● REC {os.path.basename(path)}")
        self.rec_action.setText("Stop &recording")

    def _open_log(self) -> None:
        if self.manager.connected:
            QtWidgets.QMessageBox.information(self, "Open log", "Disconnect first.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open CAN log", self.settings.value("last_dir", ""), LOG_FILTER)
        if not path:
            return
        self._reset_data()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        n = 0
        try:
            with can.LogReader(path) as reader:
                for msg in reader:
                    self.manager.process(msg)
                    n += 1
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open log", f"Error after {n} frames:\n{exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.plot.window_spin.setValue(0)  # show the whole log
        self.statusBar().showMessage(f"Imported {n} frames from {os.path.basename(path)}", 8000)

    def _export_csv(self) -> None:
        keys = self.plot.plotted() or self.store.keys()
        if not keys:
            QtWidgets.QMessageBox.information(self, "Export", "No signal data yet.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export signals", self.settings.value("last_dir", ""), "CSV (*.csv)")
        if path:
            self.store.export_csv(path, keys)
            self.statusBar().showMessage(f"Exported {len(keys)} signals to {path}", 5000)

    # ------------------------------------------------------------ tools
    def _toggle_demo(self, on: bool) -> None:
        if not on:
            if self.demo:
                self.demo.stop()
                self.demo = None
            return
        cfg = self.manager.config
        if not self.manager.connected or cfg.interface != "virtual":
            QtWidgets.QMessageBox.information(
                self, "Demo traffic", "Connect with interface 'virtual' first.")
            self.demo_action.setChecked(False)
            return
        if not self.dbc.messages():
            QtWidgets.QMessageBox.information(self, "Demo traffic", "Load a DBC first.")
            self.demo_action.setChecked(False)
            return
        self.demo = DemoTraffic(self.dbc, cfg.channel)
        self.demo.start()

    def _vector_hw_config(self) -> None:
        try:
            from can.interfaces.vector import VectorBus
            VectorBus.popup_vector_hw_configuration()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Vector", f"Could not open Vector Hardware Config:\n{exc}")

    def _about(self) -> None:
        QtWidgets.QMessageBox.about(
            self, "About",
            "<b>MichiCAN</b><br>Read, send and plot CAN signals with python-can, cantools, "
            "PySide6 and pyqtgraph.<br><br>"
            "Tick a signal in the tree to plot it. Double-click a message to open it in "
            "the Transmit tab. Drop .dbc files on the window to load them.")

    # ------------------------------------------------------------ settings
    def _save_settings(self) -> None:
        s = self.settings
        cfg = self.conn.config()
        s.setValue("interface", cfg.interface)
        s.setValue("channel", self.conn.channel.currentText())
        s.setValue("bitrate", cfg.bitrate)
        s.setValue("fd", cfg.fd)
        s.setValue("data_bitrate", cfg.data_bitrate)
        s.setValue("app_name", cfg.app_name)
        s.setValue("dbc_files", self.dbc.files)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("top_split", self.top_split.saveState())
        s.setValue("main_split", self.main_split.saveState())

    def _restore_settings(self) -> None:
        s = self.settings
        self.conn.interface.setCurrentText(s.value("interface", "vector"))
        self.conn.channel.setEditText(s.value("channel", "0"))
        self.conn.bitrate.setCurrentText(str(s.value("bitrate", 500000)))
        self.conn.fd.setChecked(s.value("fd", False, type=bool))
        self.conn.data_bitrate.setCurrentText(str(s.value("data_bitrate", 2000000)))
        self.conn.app_name.setText(s.value("app_name", ""))
        for key, w in (("geometry", self), ("top_split", self.top_split), ("main_split", self.main_split)):
            state = s.value(key)
            if state is not None:
                (w.restoreGeometry if w is self else w.restoreState)(state)
        files = s.value("dbc_files", []) or []
        if isinstance(files, str):
            files = [files]
        for f in files:
            if os.path.exists(f):
                self.load_dbc(f)

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self._save_settings()
        if self.demo:
            self.demo.stop()
        self.manager.disconnect()
        super().closeEvent(e)
