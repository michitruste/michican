"""Launch the MichiCAN GUI.

    python main.py [file.dbc ...]
"""
import logging
import sys

from PySide6 import QtGui, QtWidgets

from michican.gui.main_window import MainWindow


def dark_palette() -> QtGui.QPalette:
    R = QtGui.QPalette.ColorRole
    p = QtGui.QPalette()
    for role, color in [
        (R.Window, "#262625"), (R.WindowText, "#e8e7e1"), (R.Base, "#1a1a19"),
        (R.AlternateBase, "#222221"), (R.Text, "#e8e7e1"), (R.Button, "#2f2f2d"),
        (R.ButtonText, "#e8e7e1"), (R.Highlight, "#2a5f9e"), (R.HighlightedText, "#ffffff"),
        (R.ToolTipBase, "#2f2f2d"), (R.ToolTipText, "#e8e7e1"), (R.PlaceholderText, "#8a8980"),
        (R.Link, "#3987e5"), (R.Mid, "#3a3a38"), (R.Dark, "#151514"), (R.Light, "#4a4a47"),
    ]:
        p.setColor(role, QtGui.QColor(color))
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(QtGui.QPalette.ColorGroup.Disabled, role, QtGui.QColor("#6f6e68"))
    return p


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    win = MainWindow(dbc_paths=sys.argv[1:])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
