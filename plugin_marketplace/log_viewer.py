"""Dialog that lets the user read ``plugin.log``.

Plugin failures land in a log file because bundled GUI apps on macOS
and Windows have no stderr the user can see. The marketplace UI's
"View log" button and the main window's status-bar hint both open
this dialog.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plugin_system import plugin_log_path


class PluginLogDialog(QDialog):
    """Read-only view of ``plugin.log`` with a refresh + clear button."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        log_path: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plugin Diagnostics")
        # Generous default so a multi-line traceback isn't cramped.
        self.resize(820, 480)

        self._log_path = log_path or plugin_log_path()

        layout = QVBoxLayout(self)

        header = QLabel(
            f"Log file: <code>{self._log_path}</code>"
        )
        header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header.setWordWrap(True)
        layout.addWidget(header)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        # Monospace; tracebacks line up better that way and the
        # editor's serif theme would make them harder to scan.
        font = self._text.font()
        font.setFamily("Menlo, Consolas, monospace")
        self._text.setFont(font)
        layout.addWidget(self._text, 1)

        buttons = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.reload_log)
        buttons.addWidget(self._refresh_btn)

        self._clear_btn = QPushButton("Clear log")
        self._clear_btn.setToolTip("Empty the log file. Use after fixing a plugin issue.")
        self._clear_btn.clicked.connect(self._clear_log)
        buttons.addWidget(self._clear_btn)

        buttons.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        buttons.addWidget(close_btn)

        layout.addLayout(buttons)

        # Cmd/Ctrl-R as a power-user refresh.
        refresh_action = QAction(self)
        refresh_action.setShortcut(QKeySequence("Ctrl+R"))
        refresh_action.triggered.connect(self.reload_log)
        self.addAction(refresh_action)

        self.reload_log()

    # ----------------------------------------------------------------------
    def reload_log(self) -> None:
        """Re-read the log file. Always show the tail by default."""
        if not self._log_path.is_file():
            self._text.setPlainText("(no plugin errors recorded yet)")
            return
        try:
            content = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._text.setPlainText(f"(could not read log file: {exc})")
            return
        if not content.strip():
            self._text.setPlainText("(log file is empty)")
            return
        self._text.setPlainText(content)
        # Scroll to the bottom - newest entries are most interesting.
        bar = self._text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_log(self) -> None:
        try:
            if self._log_path.is_file():
                self._log_path.write_text("", encoding="utf-8")
        except OSError:
            # Best-effort; the next plugin failure will recreate the file.
            pass
        self.reload_log()
