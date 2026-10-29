#!/usr/bin/env python3
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("diagnostics", Path(__file__).with_name("save_private_diagnostics.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.session = self.root / "123"
        self.session.mkdir()
        self.log = self.session / "failed-build.log"
        self.log.write_text("private compiler failure")
        (self.session / "session.json").write_text('{"repository_id":123}')
        self.env = patch.dict(os.environ, {"SOURCE_REPOSITORY": module.TARGET})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_private_draft_contains_log_and_record_without_printing_them(self):
        calls = []
        def run(*args):
            calls.append(args)
            if args[:2] == ("gh", "api"):
                return json.dumps({"private": True, "full_name": module.TARGET})
            with zipfile.ZipFile(args[4]) as archive:
                self.assertEqual(archive.read('123/failed-build.log'), b'private compiler failure')
                self.assertEqual(json.loads(archive.read('123/session.json'))['repository_id'], 123)
            self.assertIn('--draft', args)
            self.assertIn('--latest=false', args)
            self.assertEqual(args[args.index('--repo') + 1], module.TARGET)
            return ''
        with patch.object(module, 'run', side_effect=run):
            module.save(self.root, '456', '1')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('private compiler failure', self.output.getvalue())

    def test_public_source_and_wrong_target_cannot_publish_logs(self):
        with patch.object(module, 'run', return_value=json.dumps({'private': False, 'full_name': module.TARGET})) as run:
            with self.assertRaises(RuntimeError):
                module.save(self.root, '456', '1')
            self.assertEqual(run.call_count, 1)
        with patch.dict(os.environ, {'SOURCE_REPOSITORY': 'zz8392570-ops/Zhmusic-releases'}), patch.object(module, 'run') as run:
            with self.assertRaises(ValueError):
                module.save(self.root, '456', '1')
            run.assert_not_called()

    def test_no_failure_log_causes_no_external_write(self):
        self.log.unlink()
        with patch.object(module, 'run') as run:
            module.save(self.root, '456', '1')
            run.assert_not_called()

    def test_run_identity_and_symlinks_are_rejected(self):
        with self.assertRaises(ValueError):
            module.save(self.root, '../unsafe', '1')
        self.log.unlink()
        self.log.symlink_to(self.session / 'session.json')
        with patch.object(module, 'run') as run:
            module.save(self.root, '456', '1')
            run.assert_not_called()

    def test_github_failure_does_not_include_command_output_in_error(self):
        import subprocess
        fake = subprocess.CompletedProcess(['gh'], 1, '', 'private compiler failure secret')
        with patch.object(module.subprocess, 'run', return_value=fake):
            with self.assertRaises(RuntimeError) as result:
                module.run('gh', 'api', 'repos/private')
        self.assertNotIn('private compiler failure secret', str(result.exception))


if __name__ == '__main__':
    unittest.main()
