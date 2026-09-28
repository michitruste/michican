"""Transmit: build frames from DBC signal values (or raw bytes), send once or cyclically."""
from __future__ import annotations

import math

import can
from PySide6 import QtCore, QtWidgets

from ..bus import CanManager
from ..dbc import DbcManager, signal_range

DBC_PREFIX = "dbc:"
RAW_PREFIX = "raw:"


def _initial_value(sig) -> float:
    if sig.raw_initial is not None:
        return float(sig.conversion.raw_to_scaled(sig.raw_initial, decode_choices=False))
    lo, hi = signal_range(sig)
    return min(max(0.0, lo), hi)


def _decimals(sig) -> int:
    if sig.scale == int(sig.scale) and sig.offset == int(sig.offset):
        return 0
    return min(6, max(1, -math.floor(math.log10(abs(sig.scale))) + 1))


class TransmitPanel(QtWidgets.QWidget):
    status = QtCore.Signal(str)

    def __init__(self, dbc: DbcManager, manager: CanManager, parent=None) -> None:
        super().__init__(parent)
        self.dbc = dbc
        self.manager = manager
        self._values: dict[str, dict[str, float]] = {}   # per message: signal -> value
        self._periods: dict[str, int] = {}                # per message: period ms
        self._rows: dict[str, int] = {}                   # signal name -> table row
        self._loading = False

        # ---------------- DBC message group
        self.msg_combo = QtWidgets.QComboBox()
        self.msg_combo.setMinimumContentsLength(28)
        self.msg_combo.setEditable(True)
        self.msg_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.msg_combo.completer().setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        self.msg_combo.completer().setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.period_spin = QtWidgets.QSpinBox(minimum=1, maximum=60_000, value=100, suffix=" ms")
        self.send_btn = QtWidgets.QPushButton("Send once")
        self.cyclic_btn = QtWidgets.QPushButton("Start cyclic", checkable=True)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Signal", "Value", "Unit", "Range"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.payload_lbl = QtWidgets.QLabel()
        self.payload_lbl.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.payload_lbl.setStyleSheet("font-family: Consolas, monospace;")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.msg_combo, 1)
        top.addWidget(self.period_spin)
        top.addWidget(self.send_btn)
        top.addWidget(self.cyclic_btn)
        dbc_box = QtWidgets.QGroupBox("DBC message")
        dl = QtWidgets.QVBoxLayout(dbc_box)
        dl.addLayout(top)
        dl.addWidget(self.table, 1)
        dl.addWidget(self.payload_lbl)

        # ---------------- raw frame group
        self.raw_id = QtWidgets.QLineEdit("123", placeholderText="hex ID")
        self.raw_ext = QtWidgets.QCheckBox("Extended")
        self.raw_fd = QtWidgets.QCheckBox("CAN FD")
        self.raw_data = QtWidgets.QLineEdit("00 00 00 00 00 00 00 00", placeholderText="hex bytes")
        self.raw_period = QtWidgets.QSpinBox(minimum=1, maximum=60_000, value=100, suffix=" ms")
        self.raw_send = QtWidgets.QPushButton("Send once")
        self.raw_cyclic = QtWidgets.QPushButton("Start cyclic")
        raw_box = QtWidgets.QGroupBox("Raw frame")
        rf = QtWidgets.QFormLayout(raw_box)
        idrow = QtWidgets.QHBoxLayout()
        idrow.addWidget(self.raw_id)
        idrow.addWidget(self.raw_ext)
        idrow.addWidget(self.raw_fd)
        rf.addRow("ID (hex)", idrow)
        rf.addRow("Data", self.raw_data)
        rf.addRow("Period", self.raw_period)
        rbtn = QtWidgets.QHBoxLayout()
        rbtn.addWidget(self.raw_send)
        rbtn.addWidget(self.raw_cyclic)
        rf.addRow(rbtn)

        # ---------------- active cyclic tasks
        self.active_list = QtWidgets.QListWidget()
        stop_sel = QtWidgets.QPushButton("Stop selected")
        stop_all = QtWidgets.QPushButton("Stop all")
        act_box = QtWidgets.QGroupBox("Active cyclic transmissions")
        al = QtWidgets.QVBoxLayout(act_box)
        al.addWidget(self.active_list)
        ab = QtWidgets.QHBoxLayout()
        ab.addWidget(stop_sel)
        ab.addWidget(stop_all)
        al.addLayout(ab)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(raw_box)
        right.addWidget(act_box, 1)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(dbc_box, 3)
        lay.addLayout(right, 2)

        self.msg_combo.currentIndexChanged.connect(self._on_message_selected)
        self.send_btn.clicked.connect(self._send_dbc_once)
        self.cyclic_btn.toggled.connect(self._toggle_dbc_cyclic)
        self.period_spin.valueChanged.connect(self._on_period_changed)
        self.raw_send.clicked.connect(self._send_raw_once)
        self.raw_cyclic.clicked.connect(self._start_raw_cyclic)
        stop_sel.clicked.connect(self._stop_selected)
        stop_all.clicked.connect(self._stop_all)
        self.set_connected(False)

    # ------------------------------------------------------------ public
    def rebuild(self) -> None:
        current = self.current_message_name()
        self.msg_combo.blockSignals(True)
        self.msg_combo.clear()
        for m in self.dbc.messages():
            self.msg_combo.addItem(f"0x{m.frame_id:X}  {m.name}", m.name)
        self.msg_combo.blockSignals(False)
        idx = self.msg_combo.findData(current) if current else 0
        self.msg_combo.setCurrentIndex(max(idx, 0))
        self._on_message_selected()

    def select_message(self, name: str) -> None:
        idx = self.msg_combo.findData(name)
        if idx >= 0:
            self.msg_combo.setCurrentIndex(idx)

    def set_connected(self, connected: bool) -> None:
        for w in (self.send_btn, self.cyclic_btn, self.raw_send, self.raw_cyclic):
            w.setEnabled(connected)
        if not connected:
            self.refresh_active()

    def current_message_name(self) -> str | None:
        return self.msg_combo.currentData()

    def refresh_active(self) -> None:
        keys = self.manager.periodic_keys()
        self.active_list.clear()
        self.active_list.addItems(keys)
        name = self.current_message_name()
        self.cyclic_btn.blockSignals(True)
        on = bool(name) and DBC_PREFIX + name in keys
        self.cyclic_btn.setChecked(on)
        self.cyclic_btn.setText("Stop cyclic" if on else "Start cyclic")
        self.cyclic_btn.blockSignals(False)

    # ------------------------------------------------------------ DBC message editing
    def _on_message_selected(self, *_):
        self._loading = True
        self.table.setRowCount(0)
        self._rows.clear()
        msg = self.dbc.by_name(self.current_message_name() or "")
        if msg is None:
            self.payload_lbl.clear()
            self._loading = False
            return
        values = self._values.setdefault(msg.name, {s.name: _initial_value(s) for s in msg.signals})
        self.period_spin.blockSignals(True)
        self.period_spin.setValue(self._periods.get(msg.name, msg.cycle_time or 100))
        self.period_spin.blockSignals(False)

        self.table.setRowCount(len(msg.signals))
        for row, sig in enumerate(msg.signals):
            self._rows[sig.name] = row
            name_item = QtWidgets.QTableWidgetItem(sig.name)
            name_item.setToolTip(sig.comment or "")
            self.table.setItem(row, 0, name_item)
            self.table.setCellWidget(row, 1, self._make_editor(msg.name, sig, values[sig.name]))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(sig.unit or ""))
            lo, hi = signal_range(sig)
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(f"{lo:g} … {hi:g}"))
        self._loading = False
        self._update_mux_visibility()
        self._update_payload()
        self.refresh_active()

    def _make_editor(self, msg_name: str, sig, value: float) -> QtWidgets.QWidget:
        def changed(v, name=sig.name):
            self._on_value_changed(msg_name, name, float(v))

        if sig.choices:
            box = QtWidgets.QComboBox()
            for k, label in sorted(sig.choices.items(), key=lambda kv: int(kv[0])):
                box.addItem(f"{int(k)}: {label}", int(k))
            idx = box.findData(int(value))
            if idx < 0:
                box.addItem(str(int(value)), int(value))
                idx = box.count() - 1
            box.setCurrentIndex(idx)
            box.currentIndexChanged.connect(lambda _i, b=box: changed(b.currentData()))
            return box

        spin = QtWidgets.QDoubleSpinBox()
        lo, hi = signal_range(sig)
        spin.setDecimals(_decimals(sig))
        spin.setRange(lo, hi)
        spin.setSingleStep(abs(sig.scale) if sig.scale else 1)
        spin.setKeyboardTracking(False)
        spin.setValue(value)
        spin.valueChanged.connect(changed)
        return spin

    def _on_value_changed(self, msg_name: str, sig_name: str, value: float) -> None:
        if self._loading:
            return
        self._values[msg_name][sig_name] = value
        msg = self.dbc.by_name(msg_name)
        if msg is not None and sig_name in {s.multiplexer_signal for s in msg.signals}:
            self._update_mux_visibility()
        frame = self._update_payload()
        if frame is not None and DBC_PREFIX + msg_name in self.manager.periodic_keys():
            self.manager.update_periodic(DBC_PREFIX + msg_name, frame)

    def _update_mux_visibility(self) -> None:
        msg = self.dbc.by_name(self.current_message_name() or "")
        if msg is None:
            return
        active = {s.name for s in self.dbc.active_signals(msg, self._values[msg.name])}
        for name, row in self._rows.items():
            self.table.setRowHidden(row, name not in active)

    def _build_frame(self) -> can.Message | None:
        msg = self.dbc.by_name(self.current_message_name() or "")
        if msg is None:
            return None
        values = self._values[msg.name]
        active = {s.name: values[s.name] for s in self.dbc.active_signals(msg, values)}
        return self.dbc.encode(msg, active)

    def _update_payload(self) -> can.Message | None:
        try:
            frame = self._build_frame()
        except Exception as exc:
            self.payload_lbl.setText(f"<span style='color:#e66767'>Encode error: {exc}</span>")
            return None
        if frame is not None:
            self.payload_lbl.setText(f"Payload: {frame.data.hex(' ').upper()}")
        return frame

    def _send_dbc_once(self) -> None:
        try:
            frame = self._build_frame()
            if frame is not None:
                self.manager.send(frame)
        except Exception as exc:
            self.status.emit(f"Send failed: {exc}")

    def _toggle_dbc_cyclic(self, on: bool) -> None:
        name = self.current_message_name()
        if not name:
            return
        key = DBC_PREFIX + name
        try:
            if on:
                frame = self._build_frame()
                self.manager.start_periodic(key, frame, self.period_spin.value() / 1000.0)
            else:
                self.manager.stop_periodic(key)
        except Exception as exc:
            self.status.emit(f"Cyclic send failed: {exc}")
        self.refresh_active()

    def _on_period_changed(self, ms: int) -> None:
        name = self.current_message_name()
        if not name:
            return
        self._periods[name] = ms
        if DBC_PREFIX + name in self.manager.periodic_keys():
            self._toggle_dbc_cyclic(True)  # restart with the new period

    # ------------------------------------------------------------ raw frames
    def _raw_frame(self) -> can.Message:
        arb_id = int(self.raw_id.text().strip().removeprefix("0x"), 16)
        data = bytes.fromhex(self.raw_data.text().replace(",", " "))
        is_fd = self.raw_fd.isChecked()
        max_len = 64 if is_fd else 8
        if len(data) > max_len:
            raise ValueError(f"{len(data)} bytes; max is {max_len}")
        return can.Message(arbitration_id=arb_id, is_extended_id=self.raw_ext.isChecked(),
                           data=data, is_fd=is_fd, bitrate_switch=is_fd)

    def _send_raw_once(self) -> None:
        try:
            self.manager.send(self._raw_frame())
        except Exception as exc:
            self.status.emit(f"Send failed: {exc}")

    def _start_raw_cyclic(self) -> None:
        try:
            frame = self._raw_frame()
            key = f"{RAW_PREFIX}0x{frame.arbitration_id:X}"
            self.manager.start_periodic(key, frame, self.raw_period.value() / 1000.0)
        except Exception as exc:
            self.status.emit(f"Cyclic send failed: {exc}")
        self.refresh_active()

    def _stop_selected(self) -> None:
        for item in self.active_list.selectedItems():
            self.manager.stop_periodic(item.text())
        self.refresh_active()

    def _stop_all(self) -> None:
        self.manager.stop_all_periodic()
        self.refresh_active()
