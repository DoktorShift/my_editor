"""Send-mode picker: Now, Schedule, or Save draft.

The schedule row uses a native ``QDateTimeEdit`` clamped between
"now + 1 minute" and "now + 365 days" so we never persist a
scheduled-for in the past or beyond Telegram's reasonable horizon.

A persistent helper label below the radio group makes the client-side
nature explicit so users are not surprised when the editor was closed
at fire time. The wording matches the plan's "Hard rule".
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


MODE_NOW: str = "now"
MODE_SCHEDULE: str = "schedule"


class ScheduleWidget(QWidget):
    """Compact 'Send: Now | Schedule' selector."""

    mode_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("Send:")
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setContentsMargins(12, 0, 0, 0)
        row.setSpacing(12)

        self._now_btn = QRadioButton("Now")
        self._now_btn.setChecked(True)
        self._now_btn.toggled.connect(self._on_toggle)
        row.addWidget(self._now_btn)

        self._schedule_btn = QRadioButton("Schedule")
        self._schedule_btn.toggled.connect(self._on_toggle)
        row.addWidget(self._schedule_btn)

        self._dt = QDateTimeEdit()
        self._dt.setCalendarPopup(True)
        self._dt.setDisplayFormat("yyyy-MM-dd  HH:mm")
        now_plus_min = QDateTime.currentDateTime().addSecs(60)
        self._dt.setMinimumDateTime(now_plus_min)
        self._dt.setMaximumDateTime(QDateTime.currentDateTime().addDays(365))
        # Default schedule: today + 1 hour, rounded to the next quarter hour.
        default = QDateTime.currentDateTime().addSecs(3600)
        minute = default.time().minute()
        rounded = (minute // 15 + 1) * 15
        if rounded >= 60:
            default = default.addSecs((60 - minute) * 60)
        else:
            default = default.addSecs((rounded - minute) * 60)
        self._dt.setDateTime(default)
        self._dt.setEnabled(False)
        row.addWidget(self._dt, 1)
        layout.addLayout(row)

        self._hint = QLabel(
            "Your editor must be running at the scheduled time. "
            "Telegram has no server-side scheduling for bots."
        )
        self._hint.setWordWrap(True)
        self._hint.setProperty("hint", True)
        self._hint.setVisible(False)
        layout.addWidget(self._hint)

    # -- public ----------------------------------------------------- #

    def mode(self) -> str:
        return MODE_SCHEDULE if self._schedule_btn.isChecked() else MODE_NOW

    def scheduled_for_unix(self) -> int:
        return int(self._dt.dateTime().toSecsSinceEpoch())

    def set_mode(self, mode: str) -> None:
        if mode == MODE_SCHEDULE:
            self._schedule_btn.setChecked(True)
        else:
            self._now_btn.setChecked(True)

    # -- internals -------------------------------------------------- #

    def _on_toggle(self) -> None:
        scheduling = self._schedule_btn.isChecked()
        self._dt.setEnabled(scheduling)
        self._hint.setVisible(scheduling)
        self.mode_changed.emit(self.mode())
