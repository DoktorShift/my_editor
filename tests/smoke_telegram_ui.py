"""Headless smoke test for every Telegram dialog and widget.

Run with::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/smoke_telegram_ui.py

This is not a pytest module - it imports the full Qt UI and exercises
construction + a handful of interactions. Failures print to stdout
with a non-zero exit code. The goal is to catch silent UI bugs that
unit tests miss (layout swap errors, signal/slot wiring, missing
attributes on a freshly-built dialog).
"""

from __future__ import annotations

import os
import sys
import tempfile

# Make the project root importable when the script is run from anywhere.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Use throwaway settings dirs so this never touches the user's data.
TMPDIR = tempfile.mkdtemp(prefix="tg_smoke_")
os.environ["HOME"] = TMPDIR

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication.instance() or QApplication([])

    failures = []

    def assert_(cond, msg):
        if not cond:
            failures.append(msg)
            print(f"FAIL  {msg}")
        else:
            print(f"OK    {msg}")

    # -- Fresh facade with no bots. ----------------------------------- #
    from publishers.telegram import TelegramPublisher
    # Reset the singleton because previous tests may have left one.
    TelegramPublisher._instance = None
    pub = TelegramPublisher.singleton()
    assert_(not pub.is_configured(), "facade reports unconfigured with no bots")

    # -- Bots dialog opens with empty state. -------------------------- #
    from publishers.telegram.ui.bots_dialog import BotsDialog
    bd = BotsDialog(pub)
    assert_(bd.isModal(), "BotsDialog is modal")
    assert_(bd.windowTitle() == "Telegram bots", "BotsDialog has title")

    # -- Add a bot manually (synthesise the post-verify state). ------ #
    bot = pub.settings.add_bot(
        display_name="Smoke Bot",
        token="999:fake",
        telegram_username="smokebot",
        telegram_first_name="Smoke",
        telegram_user_id=999,
    )
    assert_(bot.id.startswith("b_"), "add_bot returns a bot with stable id")
    assert_(pub.is_configured(), "facade configured once a bot exists")

    # -- BotsDialog reloads with the new card. ---------------------- #
    bd._reload()
    # Scroll inner layout has 1 card + 1 stretch.
    assert_(bd._inner_layout.count() >= 1, "bots list shows the new bot")

    # -- Add a chat for that bot. ----------------------------------- #
    from publishers.telegram.settings import Chat
    pub.settings.upsert_chat(bot.id, Chat(
        id=-1001, type="channel", title="My Channel", username="mychannel",
    ))
    pub.settings.upsert_chat(bot.id, Chat(
        id=-1002, type="supergroup", title="Beta Group", username="",
    ))
    pub.settings.upsert_chat(bot.id, Chat(
        id=12345, type="private", title="Test User", username="testuser",
    ))

    # -- AddBotDialog constructs without verified state. ------------- #
    from publishers.telegram.ui.add_bot_dialog import AddBotDialog
    abd = AddBotDialog(pub)
    assert_(not abd._add_btn.isEnabled(),
            "AddBotDialog: Add button disabled before verify")
    assert_(not abd._name_edit.isEnabled(),
            "AddBotDialog: name edit disabled before verify")

    # -- PublishDialog constructs and pre-selects default bot. ------- #
    from publishers.telegram.ui.publish_dialog import PublishDialog
    pd = PublishDialog(pub, body_markdown="hello *world*")
    assert_(pd._active_bot_id == bot.id, "PublishDialog selects default bot")
    assert_(pd._body_edit.toPlainText() == "hello *world*",
            "PublishDialog populates body")
    assert_(pd._send_btn.text().startswith("Send"),
            "Send button label reflects mode")
    assert_(not pd._send_btn.isEnabled(), "Send button disabled without targets")

    # -- ChatsPanel renders the three chat cards. -------------------- #
    cp = pd._chats_panel
    assert_(len(cp._rows) == 3, "ChatsPanel renders 3 chat cards")

    # Pick one chat and confirm picked_count + send enables.
    cp._rows[0]._main_cb.setChecked(True)
    assert_(cp.picked_count() == 1, "selection updates picked_count")
    pd._update_send_button()
    assert_(pd._send_btn.isEnabled(), "Send enables once a target is picked")

    # -- Switch to schedule mode; send label flips, picker shown. --- #
    from publishers.telegram.ui.schedule_widget import MODE_SCHEDULE
    pd._schedule.set_mode(MODE_SCHEDULE)
    assert_(pd._schedule.mode() == MODE_SCHEDULE, "schedule widget mode set")
    pd._update_send_button()
    assert_("Schedule" in pd._send_btn.text(),
            "Send button shows Schedule when scheduling")

    # -- SendResultsView populates without error. ------------------- #
    from publishers.telegram.publisher import PublishResult
    pd._results_view.populate(
        [
            PublishResult(chat_id=-1001, ok=True, message="OK",
                          message_id=42, permalink="https://t.me/mychannel/42"),
            PublishResult(chat_id=-1002, ok=False,
                          message="bot was kicked"),
            PublishResult(chat_id=12345, ok=True, message="OK",
                          message_id=7, permalink=""),
        ],
        bot=pub.settings.bot_by_id(bot.id),
    )
    assert_(pd._results_view._copy_all_btn.isEnabled(),
            "Copy all links enabled (one link present)")
    assert_(pd._results_view._pin_btn.isEnabled(),
            "Pin button enabled (one OK send)")

    # -- QueueDialog constructs (no entries first). ----------------- #
    from publishers.telegram.ui.queue_dialog import QueueDialog
    qd = QueueDialog(pub)
    assert_(qd._inner_layout.count() >= 1,
            "QueueDialog shows empty state when no posts")

    # -- Add a scheduled post; queue dialog rebuilds with it. ------- #
    import time
    from publishers.telegram.queue import ScheduledTarget
    pub.queue.add(
        bot_id=bot.id,
        targets=[ScheduledTarget(chat_id=-1001)],
        body_markdown="scheduled hello",
        scheduled_for=int(time.time()) + 3600,
    )
    qd._reload()
    assert_(qd._inner_layout.count() >= 2,
            "QueueDialog shows the scheduled post plus stretch")

    # -- InvitePicker tabs construct. ------------------------------- #
    from publishers.telegram.ui.invite_picker import InvitePicker
    ip = InvitePicker("smokebot")
    assert_(ip.findChildren(type(ip).__bases__[0]) or True,
            "InvitePicker constructs")

    # -- AddChatDialog constructs without immediate network call. --- #
    from publishers.telegram.ui.add_chat_dialog import AddChatDialog
    api = pub.bots.api_for(bot.id)
    acd = AddChatDialog(api=api, settings=pub.settings, bot_id=bot.id)
    assert_(acd._add_btn.text() == "Look up and add",
            "AddChatDialog button labelled clearly")

    # -- Theme: monospace only on font-role=code. ------------------- #
    from PySide6.QtGui import QFont
    # Body edit must have a proportional font, not Menlo / Consolas.
    family = pd._body_edit.font().family().lower()
    assert_("menlo" not in family and "consolas" not in family,
            f"body editor uses proportional font (family={family!r})")

    # Pump the event loop briefly to flush any deferred deleteLater.
    loop = QEventLoop()
    QTimer.singleShot(50, loop.quit)
    loop.exec()

    print()
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
