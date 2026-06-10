"""Render every Telegram dialog to a PNG for visual verification.

Output goes into ``/tmp/tg_screenshots/<dialog>.png``. The script
seeds a couple of bots and chats so the dialogs render with real
content, not empty states.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMPDIR = tempfile.mkdtemp(prefix="tg_shots_")
os.environ["HOME"] = TMPDIR

OUT = "/tmp/tg_screenshots"
os.makedirs(OUT, exist_ok=True)

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication


def _flush():
    loop = QEventLoop()
    QTimer.singleShot(80, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication.instance() or QApplication([])

    from publishers.telegram import TelegramPublisher
    from publishers.telegram.settings import Chat, Topic
    from publishers.telegram.queue import ScheduledTarget
    from publishers.telegram.publisher import PublishResult

    TelegramPublisher._instance = None
    pub = TelegramPublisher.singleton()

    # Seed two bots and several chats.
    bot1 = pub.settings.add_bot(
        display_name="Product Updates", token="111:fake1",
        telegram_username="myproductbot", telegram_first_name="My Product Updates",
        telegram_user_id=111,
    )
    bot2 = pub.settings.add_bot(
        display_name="Internal Alerts", token="222:fake2",
        telegram_username="internalalertsbot", telegram_first_name="Internal Alerts",
        telegram_user_id=222,
    )
    pub.settings.upsert_chat(bot1.id, Chat(
        id=-1001234567890, type="channel", title="Product Updates",
        username="myproductupdates", last_seen=int(time.time()) - 600,
        last_sent_at=int(time.time()) - 7200,
    ))
    pub.settings.set_chat_starred(bot1.id, -1001234567890, True)
    pub.settings.upsert_chat(bot1.id, Chat(
        id=-1001000000001, type="supergroup", title="Beta Testers",
        last_seen=int(time.time()) - 86400,
    ))
    pub.settings.upsert_chat(bot1.id, Chat(
        id=-1009876543210, type="supergroup", title="Community Forum",
        is_forum=True,
        topics=[
            Topic(id=1, name="General"),
            Topic(id=42, name="Bugs"),
            Topic(id=99, name="Feature requests"),
        ],
        last_seen=int(time.time()) - 3600,
    ))
    pub.settings.upsert_chat(bot1.id, Chat(
        id=12345, type="private", title="Drshift",
        username="drshift",
        last_seen=int(time.time()) - 86400 * 2,
    ))

    # Queue: one pending, one sent, one failed.
    p1 = pub.queue.add(
        bot_id=bot1.id,
        targets=[ScheduledTarget(chat_id=-1001234567890)],
        body_markdown="Tomorrow's release notes for v2.4",
        scheduled_for=int(time.time()) + 3600 * 24,
    )
    p2 = pub.queue.add(
        bot_id=bot1.id,
        targets=[ScheduledTarget(chat_id=-1001000000001)],
        body_markdown="Hotfix going out at noon",
        scheduled_for=int(time.time()) - 3600,
    )
    pub.queue.record_delivery(
        p2.id, chat_id=-1001000000001, ok=True, message_id=99,
        permalink="https://t.me/c/1001000000001/99",
    )
    pub.queue.finalize(p2.id)

    # Now render each dialog.
    from publishers.telegram.ui.bots_dialog import BotsDialog
    from publishers.telegram.ui.add_bot_dialog import AddBotDialog
    from publishers.telegram.ui.publish_dialog import PublishDialog
    from publishers.telegram.ui.queue_dialog import QueueDialog
    from publishers.telegram.ui.invite_picker import InvitePicker
    from publishers.telegram.ui.add_chat_dialog import AddChatDialog

    bd = BotsDialog(pub)
    bd.resize(640, 540)
    bd.show()
    _flush()
    bd.grab().save(os.path.join(OUT, "01_bots_dialog.png"))
    print(f"saved {OUT}/01_bots_dialog.png")

    abd = AddBotDialog(pub)
    abd.resize(560, 580)
    abd.show()
    _flush()
    abd.grab().save(os.path.join(OUT, "02_add_bot_dialog_initial.png"))
    print(f"saved {OUT}/02_add_bot_dialog_initial.png")

    pd = PublishDialog(pub, body_markdown=(
        "## Release v2.4\n\n"
        "**New features**\n"
        "- Dark mode polish\n"
        "- Drag-and-drop image upload\n\n"
        "**Fixes**\n"
        "- Crash on Linux when opening preferences\n"
        "- Memory leak in long sessions\n\n"
        "Full notes: https://example.com/v2-4"
    ))
    pd.resize(1000, 660)
    pd.show()
    _flush()
    pd.grab().save(os.path.join(OUT, "03_publish_dialog_compose.png"))
    print(f"saved {OUT}/03_publish_dialog_compose.png")

    # Force a results view by populating directly.
    pd._results_view.populate(
        [
            PublishResult(chat_id=-1001234567890, ok=True, message="OK",
                          message_id=482, permalink="https://t.me/myproductupdates/482"),
            PublishResult(chat_id=-1001000000001, ok=True, message="OK",
                          message_id=119, permalink="https://t.me/c/1001000000001/119"),
            PublishResult(chat_id=12345, ok=False, message="bot was blocked by the user"),
        ],
        bot=pub.settings.bot_by_id(bot1.id),
    )
    pd._stack.setCurrentWidget(pd._results_view)
    _flush()
    pd.grab().save(os.path.join(OUT, "04_publish_dialog_results.png"))
    print(f"saved {OUT}/04_publish_dialog_results.png")

    qd = QueueDialog(pub)
    qd.resize(760, 540)
    qd.show()
    _flush()
    qd.grab().save(os.path.join(OUT, "05_queue_dialog.png"))
    print(f"saved {OUT}/05_queue_dialog.png")

    ip = InvitePicker("myproductbot")
    ip.resize(640, 560)
    ip.show()
    _flush()
    ip.grab().save(os.path.join(OUT, "06_invite_picker.png"))
    print(f"saved {OUT}/06_invite_picker.png")

    api = pub.bots.api_for(bot1.id)
    acd = AddChatDialog(api=api, settings=pub.settings, bot_id=bot1.id)
    acd.resize(500, 320)
    acd.show()
    _flush()
    acd.grab().save(os.path.join(OUT, "07_add_chat_dialog.png"))
    print(f"saved {OUT}/07_add_chat_dialog.png")

    return 0


if __name__ == "__main__":
    sys.exit(main())
