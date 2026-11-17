"""The Nextendo account row: the four states it can be in, and what each one says.

Needs PySide6 and runs offscreen, skipped when PySide6 is absent.

    QT_QPA_PLATFORM=offscreen python3 -m unittest frontend-qt/tests/test_nextendo_row.py -v

No browser, no network: `nextendo_auth.sign_in` is replaced, and the token file lives in a
temporary folder instead of the launcher's data folder.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'frontend-qt'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:          # the packaged app always has it, a bare python3 may not
    QApplication = None

from nspb_qt import nextendo_auth as na  # noqa: E402


@unittest.skipIf(QApplication is None, 'PySide6 is not installed')
class NextendoRow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app_path = Path(self.tmp.name) / 'nextendo-app.json'
        self.token_path = Path(self.tmp.name) / 'nextendo-token.json'
        for target, value in (('default_app_path', lambda: self.app_path),
                              ('default_token_path', lambda: self.token_path)):
            p = mock.patch.object(na, target, value)
            p.start()
            self.addCleanup(p.stop)

    def _window(self):
        from nspb_qt.i18n import Lang
        from nspb_qt.model import Model
        from nspb_qt.ui import MainWindow
        patches = [mock.patch.object(Model, 'check_deps', lambda self: None),
                   mock.patch.object(Model, 'repo_ok', new_callable=mock.PropertyMock, return_value=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        m = Model()
        m.lang = Lang('en')
        w = MainWindow(m)
        self.addCleanup(self._delete, m, w)
        w.show()
        for _ in range(3):
            self.app.processEvents()
        return m, w

    def _delete(self, m, w):
        w.close()
        w.deleteLater()
        m.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def _settle(self, m, timeout=5.0):
        end = time.monotonic() + timeout
        while m.nextendo_busy and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()

    def test_without_an_app_file_the_row_says_how_to_make_one_and_the_button_is_off(self):
        m, w = self._window()
        self.assertFalse(w.nextendo_btn.isEnabled())
        self.assertIn('nextendo.network/developers', w.nextendo_note_lbl.text())
        self.assertEqual(w.nextendo_status_lbl.text(), 'Not signed in')

    def test_a_successful_sign_in_writes_the_token_and_names_the_account(self):
        self.app_path.write_text(json.dumps({'client_id': 'nxc_test'}))
        m, w = self._window()
        self.assertTrue(w.nextendo_btn.isEnabled())
        self.assertIn('Documents', w.nextendo_note_lbl.text())

        with mock.patch.object(na, 'sign_in', lambda cfg, **kw: {'access_token': 't', 'name': 'Mattia'}):
            w.nextendo_btn.click()
            self._settle(m)

        self.assertEqual(w.nextendo_status_lbl.text(), 'Signed in as Mattia')
        self.assertTrue(self.token_path.is_file())
        self.assertEqual(na.read_token_file(self.token_path)['access_token'], 't')
        self.assertFalse(w.nextendo_show_btn.isHidden())

    def test_a_failed_sign_in_shows_what_nextendo_said(self):
        self.app_path.write_text(json.dumps({'client_id': 'nxc_test'}))
        m, w = self._window()

        def boom(cfg, **kw):
            raise na.AuthError('the sign-in window was closed')

        with mock.patch.object(na, 'sign_in', boom):
            w.nextendo_btn.click()
            self._settle(m)

        self.assertIn('Sign-in failed', w.nextendo_status_lbl.text())
        self.assertIn('window was closed', w.nextendo_status_lbl.text())
        self.assertFalse(self.token_path.exists())

    def test_a_token_from_a_previous_run_starts_the_row_signed_in(self):
        self.token_path.write_text(json.dumps({'access_token': 'old', 'name': 'Mattia'}))
        m, w = self._window()
        self.assertEqual(w.nextendo_status_lbl.text(), 'Signed in as Mattia')
        self.assertFalse(w.nextendo_show_btn.isHidden())


if __name__ == '__main__':
    unittest.main()
