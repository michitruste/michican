"""Toolbar widget with the interface / channel / bitrate settings and Connect button."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..bus import BITRATES, DATA_BITRATES, INTERFACES, BusConfig, describe_channel, detect_channels


class ConnectionBar(QtWidgets.QWidget):
    connect_toggled = QtCore.Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.interface = QtWidgets.QComboBox()
        self.interface.addItems(INTERFACES)
        self.channel = QtWidgets.QComboBox(editable=True)
        self.channel.setMinimumContentsLength(24)
        self.channel.setToolTip("Vector: channel index (see Detect / Vector Hardware Config).\n"
                                "virtual: any name, e.g. vcan0")
        self.detect_btn = QtWidgets.QPushButton("Detect")
        self.detect_btn.setToolTip("List channels found by the driver")
        self.bitrate = QtWidgets.QComboBox(editable=True)
        self.bitrate.addItems([str(b) for b in BITRATES])
        self.bitrate.setCurrentText("500000")
        self.fd = QtWidgets.QCheckBox("FD")
        self.data_bitrate = QtWidgets.QComboBox(editable=True)
        self.data_bitrate.addItems([str(b) for b in DATA_BITRATES])
        self.data_bitrate.setCurrentText("2000000")
        self.data_bitrate.setEnabled(False)
        self.app_name = QtWidgets.QLineEdit(placeholderText="App name (optional)")
        self.app_name.setMaximumWidth(150)
        self.app_name.setToolTip("Empty: channel is the global Vector channel index.\n"
                                 "Set e.g. 'CANalyzer' to use that application's channel mapping.")
        self.listen_only = QtWidgets.QCheckBox("Listen only")
        self.connect_btn = QtWidgets.QPushButton("Connect", checkable=True)
        self.connect_btn.setMinimumWidth(100)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        for label, w in [("Interface", self.interface), ("Channel", self.channel)]:
            lay.addWidget(QtWidgets.QLabel(label))
            lay.addWidget(w)
        lay.addWidget(self.detect_btn)
        lay.addWidget(QtWidgets.QLabel("Bitrate"))
        lay.addWidget(self.bitrate)
        lay.addWidget(self.fd)
        lay.addWidget(self.data_bitrate)
        lay.addWidget(self.app_name)
        lay.addWidget(self.listen_only)
        lay.addWidget(self.connect_btn)

        self.fd.toggled.connect(self.data_bitrate.setEnabled)
        self.interface.currentTextChanged.connect(self._on_interface)
        self.detect_btn.clicked.connect(self.detect)
        self.connect_btn.toggled.connect(self._on_connect)
        self._on_interface(self.interface.currentText())

    def config(self) -> BusConfig:
        channel = self.channel.currentText().strip()
        idx = self.channel.findText(channel)
        if idx >= 0 and self.channel.itemData(idx) is not None:
            channel = str(self.channel.itemData(idx))
        return BusConfig(
            interface=self.interface.currentText(),
            channel=channel,
            bitrate=int(self.bitrate.currentText()),
            fd=self.fd.isChecked(),
            data_bitrate=int(self.data_bitrate.currentText()),
            app_name=self.app_name.text().strip(),
            listen_only=self.listen_only.isChecked(),
        )

    def set_connected(self, connected: bool) -> None:
        self.connect_btn.blockSignals(True)
        self.connect_btn.setChecked(connected)
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.connect_btn.blockSignals(False)
        for w in (self.interface, self.channel, self.detect_btn, self.bitrate, self.fd,
                  self.data_bitrate, self.app_name, self.listen_only):
            w.setEnabled(not connected)
        if not connected:
            self.data_bitrate.setEnabled(self.fd.isChecked())
            self._on_interface(self.interface.currentText())

    def detect(self) -> None:
        iface = self.interface.currentText()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            configs = detect_channels(iface)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not configs:
            hint = ("\n\nIs the Vector driver / XL Driver Library installed and the "
                    "CANcase plugged in?") if iface == "vector" else ""
            QtWidgets.QMessageBox.information(self, "Detect", f"No {iface} channels found.{hint}")
            return
        self.channel.clear()
        for cfg in configs:
            value = cfg.get("channel_index", cfg.get("channel"))
            self.channel.addItem(describe_channel(cfg), value)
        if iface == "vector":
            self.app_name.clear()  # detected indexes are global channel indexes

    def _on_interface(self, iface: str) -> None:
        is_vector = iface == "vector"
        self.app_name.setVisible(is_vector)
        self.listen_only.setVisible(is_vector)
        is_virtual = iface == "virtual"
        self.bitrate.setEnabled(not is_virtual)
        self.fd.setEnabled(not is_virtual)
        if is_virtual and not self.channel.currentText():
            self.channel.setEditText("vcan0")

    def _on_connect(self, checked: bool) -> None:
        self.connect_toggled.emit(checked)
