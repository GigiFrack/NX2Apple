"""Tests for the headless build (`python -m nspb_qt.win.build`): the same two steps the launcher runs
when it packs the shared shell IPA, with no window in the way.

The driver is never run: the command line it would receive is checked, and the packing step is
checked against shell_export's own helpers with `export_shell` replaced.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nspb_qt.win import build as hb  # noqa: E402
from nspb_qt.win import shell_export as se  # noqa: E402


class DriverCommandTests(unittest.TestCase):
    def test_the_command_names_the_game_the_version_and_the_shell_framework(self) -> None:
        ap_args = mock.Mock(
            nsp_dir='C:/games', keys='C:/keys/prod.keys', title_id='0100965017338000',
            package='v458752', driver_args=[])
        cmd = hb._driver_command(ap_args, Path('C:/build'))
        joined = ' '.join(cmd)
        self.assertIn('-m nspb_qt.win.driver', joined)
        self.assertIn('--title-id 0100965017338000', joined)
        self.assertIn('-p v458752', joined)
        self.assertIn('--work-dir', joined)
        # the framework goes into <work>/shell-framework, which is where the packer looks for it
        self.assertIn(str(Path('C:/build') / 'shell-framework'), joined)

    def test_extra_arguments_reach_the_driver_unchanged(self) -> None:
        ap_args = mock.Mock(
            nsp_dir='C:/games', keys='', title_id='', package='', driver_args=['--from-seed'])
        joined = ' '.join(hb._driver_command(ap_args, None))
        self.assertIn('--from-seed', joined)
        self.assertNotIn('--work-dir', joined, 'no build folder: the driver picks its own')

    def test_the_modules_own_flags_are_not_handed_to_the_driver(self) -> None:
        # `--` and everything after it is the driver's; our flags stay ours (a REMAINDER positional
        # used to swallow them, which passed --work-dir to the driver twice and ignored ours)
        with mock.patch.object(hb, 'build_framework', return_value=0) as build:
            rc = hb.main(['x', '--skip-pack', '--title-id', '0100965017338000', '--', '--from-seed'])
        self.assertEqual(rc, 0)
        args = build.call_args[0][0]
        self.assertEqual(args.driver_args, ['--from-seed'])
        self.assertTrue(args.skip_pack)


class PackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix='qt-headless-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / '0100965017338000_build'
        self.fw = self.work / 'shell-framework' / 'DmGame0100965017338000.framework'
        self.fw.mkdir(parents=True)
        (self.fw / 'game.manifest.json').write_text('{"name": "Super Mario Party Jamboree"}', encoding='utf-8')

    def _args(self, **kw):
        # a Mock would take `name=` for its own: the parser's namespace is what pack() reads
        base = dict(title_id='0100965017338000', name=None, shell_root=str(self.tmp / 'shell'),
                    print_path_file=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_the_framework_found_is_the_one_packed(self) -> None:
        seen: dict[str, object] = {}

        def fake_export(**kwargs):
            seen.update(kwargs)
            return True, str(self.tmp / 'NX2Apple.ipa'), '', []

        with mock.patch.object(se, 'export_shell', side_effect=fake_export):
            rc = hb.pack(self._args(), self.work, self.tmp / 'engine')
        self.assertEqual(rc, 0)
        self.assertEqual(Path(seen['framework']), self.fw)
        self.assertEqual(seen['title_id'], '0100965017338000')
        self.assertEqual(seen['name'], 'Super Mario Party Jamboree')

    def test_no_framework_is_a_failure_not_an_empty_ipa(self) -> None:
        empty = self.tmp / 'empty_build'
        (empty / 'shell-framework').mkdir(parents=True)
        with mock.patch.object(se, 'export_shell') as export:
            rc = hb.pack(self._args(), empty, self.tmp / 'engine')
        self.assertEqual(rc, 2)
        export.assert_not_called()

    def test_the_ipa_path_is_written_for_a_script(self) -> None:
        out = self.tmp / 'ipa_path.txt'
        with mock.patch.object(se, 'export_shell',
                               return_value=(True, str(self.tmp / 'NX2Apple.ipa'), '', [])):
            rc = hb.pack(self._args(print_path_file=str(out)), self.work, self.tmp / 'engine')
        self.assertEqual(rc, 0)
        self.assertEqual(out.read_text(encoding='utf-8'), str(self.tmp / 'NX2Apple.ipa'))

    def test_a_failed_pack_does_not_claim_success(self) -> None:
        with mock.patch.object(se, 'export_shell', return_value=(False, '', 'no template', [])):
            rc = hb.pack(self._args(), self.work, self.tmp / 'engine')
        self.assertEqual(rc, 3)


if __name__ == '__main__':
    unittest.main()
