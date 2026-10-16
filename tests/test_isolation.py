"""The tests keep out of the person's own files."""

import os

import recovery


def test_no_test_backs_up_into_the_persons_home():
    """A window a test builds backs up what is typed in it, as the app does.
    Into the person's own ~/.cache, MyEditor would offer every one of them
    back at its next launch (thousands, after a few hundred test runs)."""
    real = os.path.join(os.path.expanduser("~"), ".cache", "my_editor", "backups")
    assert os.path.abspath(recovery.BACKUP_DIR) != os.path.abspath(real)
