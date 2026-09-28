"""Frame trace: fixed (one row per ID) or scrolling (chronological) view."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from ..bus import RxItem
from .signal_tree import format_value

COLUMNS = ["Time (s)", "Dir", "ID", "Name", "DLC", "Data", "Cycle (ms)", "Count", "Signals"]
C_TIME, C_DIR, C_ID, C_NAME, C_DLC, C_DATA, C_CYCLE, C_COUNT, C_SIGNALS = range(len(COLUMNS))
SORT_ROLE = QtCore.Qt.ItemDataRole.UserRole


@dataclass
class Row:
    item: RxItem
    count: int = 1
    period_ms: float | None = None


def _key(item: RxItem):
    m = item.msg
    return (m.arbitration_id, m.is_extended_id, m.is_rx, m.is_error_frame)


class TraceModel(QtCore.QAbstractTableModel):
    def __init__(self, max_rows: int = 20_000, parent=None) -> None:
        super().__init__(parent)
        self.max_rows = max_rows
        self.fixed = True
        self.rows: list[Row] = []
        self._index: dict[tuple, int] = {}
        self._last: dict[tuple, tuple[float, int]] = {}  # key -> (t, count), for scrolling mode
        self._mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)

    # ------------------------------------------------------------ Qt model API
    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        row = self.rows[index.row()]
        m, col = row.item.msg, index.column()
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self._text(row, col)
        if role == SORT_ROLE:
            if col == C_ID:
                return m.arbitration_id + (1 << 32 if m.is_extended_id else 0)
            if col == C_TIME:
                return row.item.t
            if col == C_COUNT:
                return row.count
            return self._text(row, col)
        if role == QtCore.Qt.ItemDataRole.FontRole and col in (C_ID, C_DATA):
            return self._mono
        if role == QtCore.Qt.ItemDataRole.TextAlignmentRole and col in (C_TIME, C_DLC, C_CYCLE, C_COUNT):
            return int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        if role == QtCore.Qt.ItemDataRole.ForegroundRole and m.is_error_frame:
            return QtGui.QColor("#e66767")
        return None

    def _text(self, row: Row, col: int) -> str:
        it, m = row.item, row.item.msg
        if col == C_TIME:
            return f"{it.t:.4f}"
        if col == C_DIR:
            return "Err" if m.is_error_frame else ("Rx" if m.is_rx else "Tx")
        if col == C_ID:
            return f"{m.arbitration_id:08X}x" if m.is_extended_id else f"{m.arbitration_id:03X}"
        if col == C_NAME:
            return it.decoded.message.name if it.decoded else ""
        if col == C_DLC:
            return str(m.dlc)
        if col == C_DATA:
            return m.data.hex(" ").upper() if m.data else ""
        if col == C_CYCLE:
            return f"{row.period_ms:.1f}" if row.period_ms is not None else ""
        if col == C_COUNT:
            return str(row.count)
        if col == C_SIGNALS and it.decoded:
            dm = it.decoded.message
            parts = []
            for name, v in it.decoded.values.items():
                sig = dm.get_signal_by_name(name)
                parts.append(f"{name}={format_value(sig, v)}{(' ' + sig.unit) if sig.unit else ''}")
            return ", ".join(parts)
        return ""

    # ------------------------------------------------------------ feeding
    def set_fixed(self, fixed: bool) -> None:
        self.fixed = fixed
        self.clear()

    def clear(self) -> None:
        self.beginResetModel()
        self.rows.clear()
        self._index.clear()
        self._last.clear()
        self.endResetModel()

    def add_batch(self, items: list[RxItem]) -> None:
        if not items:
            return
        if self.fixed:
            self._add_fixed(items)
        else:
            self._add_scrolling(items)

    def _add_fixed(self, items: list[RxItem]) -> None:
        lo, hi = None, None
        pending: dict[tuple, Row] = {}
        for it in items:
            key = _key(it)
            idx = self._index.get(key)
            row = self.rows[idx] if idx is not None else pending.get(key)
            if row is None:
                pending[key] = Row(it)
                continue
            row.period_ms = (it.t - row.item.t) * 1000.0
            row.item = it
            row.count += 1
            if idx is not None:
                lo = idx if lo is None else min(lo, idx)
                hi = idx if hi is None else max(hi, idx)
        if lo is not None:
            self.dataChanged.emit(self.index(lo, 0), self.index(hi, len(COLUMNS) - 1))
        if pending:
            n = len(self.rows)
            self.beginInsertRows(QtCore.QModelIndex(), n, n + len(pending) - 1)
            for key, row in pending.items():
                self._index[key] = len(self.rows)
                self.rows.append(row)
            self.endInsertRows()

    def _add_scrolling(self, items: list[RxItem]) -> None:
        new = []
        for it in items[-self.max_rows:]:
            key = _key(it)
            prev = self._last.get(key)
            count = prev[1] + 1 if prev else 1
            period = (it.t - prev[0]) * 1000.0 if prev else None
            self._last[key] = (it.t, count)
            new.append(Row(it, count, period))
        overflow = len(self.rows) + len(new) - self.max_rows
        if overflow > 0:
            self.beginRemoveRows(QtCore.QModelIndex(), 0, overflow - 1)
            del self.rows[:overflow]
            self.endRemoveRows()
        n = len(self.rows)
        self.beginInsertRows(QtCore.QModelIndex(), n, n + len(new) - 1)
        self.rows.extend(new)
        self.endInsertRows()


class TraceView(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = TraceModel(parent=self)
        self.proxy = QtCore.QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Fixed (one row per ID)", "Scrolling (chronological)"])
        self.pause_chk = QtWidgets.QCheckBox("Pause")
        self.autoscroll_chk = QtWidgets.QCheckBox("Auto-scroll", checked=True)
        self.filter_edit = QtWidgets.QLineEdit(placeholderText="Filter (ID, name, data…)")
        self.filter_edit.setClearButtonEnabled(True)
        clear_btn = QtWidgets.QPushButton("Clear")

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(self.mode_combo)
        bar.addWidget(self.pause_chk)
        bar.addWidget(self.autoscroll_chk)
        bar.addWidget(self.filter_edit, 1)
        bar.addWidget(clear_btn)

        self.view = QtWidgets.QTableView()
        self.view.setModel(self.proxy)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(20)
        self.view.horizontalHeader().setStretchLastSection(True)
        for col, w in [(C_TIME, 90), (C_DIR, 40), (C_ID, 90), (C_NAME, 150), (C_DLC, 40),
                       (C_DATA, 260), (C_CYCLE, 80), (C_COUNT, 70)]:
            self.view.setColumnWidth(col, w)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.view)

        self.mode_combo.currentIndexChanged.connect(self._on_mode)
        self.filter_edit.textChanged.connect(self.proxy.setFilterFixedString)
        clear_btn.clicked.connect(self.model.clear)
        self._on_mode(0)

    def add_batch(self, items: list[RxItem]) -> None:
        if self.pause_chk.isChecked():
            return
        self.model.add_batch(items)
        if not self.model.fixed and self.autoscroll_chk.isChecked():
            self.view.scrollToBottom()

    def clear(self) -> None:
        self.model.clear()

    def _on_mode(self, index: int) -> None:
        fixed = index == 0
        self.model.set_fixed(fixed)
        self.autoscroll_chk.setEnabled(not fixed)
        self.view.setSortingEnabled(fixed)
        if fixed:
            self.view.sortByColumn(C_ID, QtCore.Qt.SortOrder.AscendingOrder)
        else:
            self.proxy.sort(-1)  # keep arrival order
