# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that Publish as Article fits a small screen, title bar and buttons
included (popup_placement.fitting_size).

The window is opened on a real 1024 by 640 screen: Qt's offscreen platform
is given one through its configuration file. Its default screen is 800 by
800, where the full 760 by 680 fits, so measuring the window there and
comparing it with a smaller screen it never had says nothing (the merge
review's probe did that).

Qt reads the platform's options split at every colon, so the file is
named from its own folder: a full path breaks at the colon of a Windows
drive (``C:\\...``), and the platform then stops without a word.
"""

import json
import sys

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, pathlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
config = pathlib.Path(sys.argv[2])
os.chdir(config.parent)                       # Qt splits its options at every colon
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + config.name
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
from tests.test_publish_job import _dialog
dialog = _dialog("article", pathlib.Path(tempfile.mkdtemp(dir=os.environ["HOME"])), None)
dialog.show()
app.processEvents()
screen = app.primaryScreen().availableGeometry()
frame = dialog.frameGeometry()
publish = dialog._publish_btn
r = {"screen": [screen.width(), screen.height()],
     "frame": [frame.left(), frame.top(), frame.right(), frame.bottom()],
     "publish_bottom": publish.mapToGlobal(publish.rect().bottomLeft()).y()}
dialog.close()
print("RESULT " + json.dumps(r))
"""


def test_publish_as_article_fits_a_1024_by_640_screen(tmp_path):
    # Where the configuration lives has a colon in its path, as every
    # Windows path has at its drive; elsewhere a folder's name gives it one.
    folder = tmp_path if sys.platform == "win32" else tmp_path / "drive:like"
    folder.mkdir(exist_ok=True)
    config = folder / "screen.json"
    config.write_text(json.dumps({"screens": [{
        "name": "small", "x": 0, "y": 0, "width": 1024, "height": 640,
        "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}), encoding="utf-8")
    r = run_window_script(SCRIPT, tmp_path, str(config))
    assert r["screen"] == [1024, 640]
    left, top, right, bottom = r["frame"]
    assert left >= 0 and top >= 0 and right < 1024 and bottom < 640, r
    assert r["publish_bottom"] < 640                    # Publish stays on screen
