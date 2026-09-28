"""DBC browser: messages and signals with live values; tick a signal to plot it."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..dbc import DbcManager
from ..signal_store import SignalStore

KEY_ROLE = QtCore.Qt.ItemDataRole.UserRole


def format_value(sig, value: float) -> str:
    if sig.choices and int(value) in sig.choices:
        return f"{int(value)} ({sig.choices[int(value)]})"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


class SignalTree(QtWidgets.QWidget):
    plot_toggled = QtCore.Signal(str, str, bool)   # key, unit, checked
    message_activated = QtCore.Signal(str)         # message name (double-click)

    def __init__(self, dbc: DbcManager, store: SignalStore, parent=None) -> None:
        super().__init__(parent)
        self.dbc = dbc
        self.store = store
        self._signal_items: dict[str, dict[str, QtWidgets.QTreeWidgetItem]] = {}
        self._msg_items: dict[str, QtWidgets.QTreeWidgetItem] = {}

        self.filter_edit = QtWidgets.QLineEdit(placeholderText="Filter messages / signals…")
        self.filter_edit.setClearButtonEnabled(True)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Value", "Unit"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(1, 120)
        self.tree.setColumnWidth(2, 50)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.filter_edit)
        lay.addWidget(self.tree)

        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.filter_edit.textChanged.connect(self._apply_filter)

    def rebuild(self, plotted: set[str]) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._signal_items.clear()
        self._msg_items.clear()
        for msg in self.dbc.messages():
            fid = f"0x{msg.frame_id:X}{'x' if msg.is_extended_frame else ''}"
            mi = QtWidgets.QTreeWidgetItem([f"{fid}  {msg.name}", "", ""])
            mi.setToolTip(0, f"{msg.name}  DLC {msg.length}  cycle {msg.cycle_time or '-'} ms\n"
                             f"Senders: {', '.join(msg.senders) or '-'}\n{msg.comment or ''}")
            mi.setData(0, KEY_ROLE, None)
            self._msg_items[msg.name] = mi
            items = {}
            for sig in msg.signals:
                key = f"{msg.name}.{sig.name}"
                si = QtWidgets.QTreeWidgetItem([sig.name, "", sig.unit or ""])
                si.setData(0, KEY_ROLE, key)
                si.setFlags(si.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                si.setCheckState(0, QtCore.Qt.CheckState.Checked if key in plotted
                                 else QtCore.Qt.CheckState.Unchecked)
                rng = f"[{sig.minimum} … {sig.maximum}]" if sig.minimum is not None else ""
                si.setToolTip(0, f"{key}\nstart {sig.start}  len {sig.length}  "
                                 f"factor {sig.scale}  offset {sig.offset}  {rng}\n{sig.comment or ''}")
                mi.addChild(si)
                items[sig.name] = si
            self._signal_items[msg.name] = items
            self.tree.addTopLevelItem(mi)
        self.tree.blockSignals(False)
        self._apply_filter(self.filter_edit.text())

    def set_plotted(self, plotted: set[str]) -> None:
        self.tree.blockSignals(True)
        for items in self._signal_items.values():
            for si in items.values():
                want = si.data(0, KEY_ROLE) in plotted
                si.setCheckState(0, QtCore.Qt.CheckState.Checked if want
                                 else QtCore.Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)

    def update_values(self, message_names: set[str], counts: dict[str, int]) -> None:
        """Refresh the Value column for messages received since the last call."""
        self.tree.blockSignals(True)
        for name in message_names:
            items = self._signal_items.get(name)
            msg = self.dbc.by_name(name)
            if items is None or msg is None:
                continue
            mi = self._msg_items[name]
            mi.setText(1, f"{counts.get(name, 0)} frames")
            if not mi.isExpanded() or mi.isHidden():
                continue  # don't spend time on collapsed rows
            for sig in msg.signals:
                latest = self.store.latest(f"{name}.{sig.name}")
                if latest is not None:
                    items[sig.name].setText(1, format_value(sig, latest[1]))
        self.tree.blockSignals(False)

    # ------------------------------------------------------------ slots
    def _on_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        key = item.data(0, KEY_ROLE)
        if column != 0 or not key:
            return
        checked = item.checkState(0) == QtCore.Qt.CheckState.Checked
        self.plot_toggled.emit(key, item.text(2), checked)

    def _on_double_click(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if item.data(0, KEY_ROLE) is None:  # message row
            self.message_activated.emit(item.text(0).split(maxsplit=1)[1])

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            mi = self.tree.topLevelItem(i)
            msg_match = not text or text in mi.text(0).lower()
            any_child = False
            for j in range(mi.childCount()):
                ci = mi.child(j)
                show = msg_match or text in ci.text(0).lower()
                ci.setHidden(not show)
                any_child |= show
            mi.setHidden(not (msg_match or any_child))
            if text and any_child and not msg_match:
                mi.setExpanded(True)
