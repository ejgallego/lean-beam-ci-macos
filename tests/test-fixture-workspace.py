#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Workspace disposition preserves failures and only removes owned successful scratch trees."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / 'tests/lib/tmp-guards.sh'
# Match the helper's owned-path contract even when the OS default TMPDIR is elsewhere.


class FixtureWorkspaceTest(unittest.TestCase):
    def finish(self, path, status, *, prefix='beam-fixture-workspace', failed_cleanup=False):
        script = '''
set -eu
. "$1"
'''
        if failed_cleanup:
            script += 'beam_test_remove_owned_tmp_tree() { return 57; }\n'
        script += '''
trap 'fixture_status=$?; beam_test_finish_workspace "$fixture_status" "$2" "$4"; exit "$?"' EXIT
exit "$3"
'''
        return subprocess.run(['bash', '-c', script, 'workspace-test', str(HELPER), str(path),
                               str(status), prefix], cwd=ROOT, text=True, capture_output=True, timeout=5)

    def test_success_removes_owned_workspace(self):
        with tempfile.TemporaryDirectory(prefix='beam-fixture-workspace-', dir='/tmp') as directory:
            path = Path(directory) / 'case'
            path.mkdir()
            (path / 'evidence').write_text('source bytes')
            result = self.finish(path, 0)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(path.exists())

    def test_failure_keeps_original_bytes_and_exit_status(self):
        with tempfile.TemporaryDirectory(prefix='beam-fixture-workspace-', dir='/tmp') as directory:
            path = Path(directory)
            (path / 'evidence').write_bytes(b'original\x00bytes')
            result = self.finish(path, 23)
            self.assertEqual(result.returncode, 23)
            self.assertIn(str(path), result.stderr)
            self.assertEqual((path / 'evidence').read_bytes(), b'original\x00bytes')

    def test_failed_success_cleanup_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory(prefix='beam-fixture-workspace-', dir='/tmp') as directory:
            result = self.finish(directory, 0, failed_cleanup=True)
            self.assertEqual(result.returncode, 57)
            self.assertTrue(Path(directory).exists())

    def test_failed_case_does_not_attempt_destructive_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='beam-fixture-workspace-', dir='/tmp') as directory:
            result = self.finish(directory, 23, failed_cleanup=True)
            self.assertEqual(result.returncode, 23)
            self.assertTrue(Path(directory).exists())

    def test_save_replay_retains_scenario_logs_on_failure(self):
        # Exercise the real script and its EXIT cleanup; no Lean build or daemon is needed.
        names = ['build.log', 'blocked-save.out', 'blocked-save.err',
                 'unsupported-save.out', 'unsupported-save.err',
                 'cancel-close-save.out', 'cancel-close-save.err',
                 'stale-trace-save.out', 'stale-trace-save.err',
                 'stale-sync.out', 'stale-sync.err', 'stale-save.out', 'stale-save.err']
        with tempfile.TemporaryDirectory(prefix='beam-fixture-workspace-', dir='/tmp') as directory:
            root = Path(directory)
            binaries = root / 'bin'
            binaries.mkdir()
            stub = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
if name == 'elan':
    print(Path(sys.argv[0]).parent / sys.argv[-1])
elif '--version' in sys.argv or '--githash' in sys.argv:
    print('fixture SDK')
else:
    workspace = Path.cwd().parent
    Path(os.environ['FIXTURE_WORKSPACE_RECEIPT']).write_text(str(workspace))
    for name in json.loads(os.environ['FIXTURE_SCENARIO_LOGS']):
        path = workspace / 'evidence' / name
        if path.is_file():
            path.write_bytes((name + '\\n').encode() + b'\\x00raw')
    raise SystemExit(23)
"""
            for name in ['elan', 'lean', 'lake']:
                command = binaries / name
                command.write_text(stub)
                command.chmod(0o755)
            installation = root / 'installation'
            (installation / 'bin').mkdir(parents=True)
            wrapper = installation / 'bin/lean-beam'
            wrapper.write_text('#!/bin/sh\nexit 0\n')
            wrapper.chmod(0o755)
            (installation / 'manifest.json').write_text('{}\n')
            receipt = root / 'workspace'
            result = subprocess.run(['bash', 'tests/test-beam-save-olean.sh'], cwd=ROOT,
                env=dict(os.environ, PATH=str(binaries)+os.pathsep+os.environ['PATH'],
                         BEAM_TEST_INSTALLATION=str(installation),
                         FIXTURE_WORKSPACE_RECEIPT=str(receipt),
                         FIXTURE_SCENARIO_LOGS=json.dumps(names)),
                text=True, capture_output=True, timeout=30)
            self.assertTrue(receipt.is_file(), result.stderr)
            workspace = Path(receipt.read_text())
            self.assertEqual(workspace.parent.resolve(), Path('/tmp').resolve())
            self.assertTrue(workspace.name.startswith('beam-save-olean-'))
            packet = ROOT / '.beam/test-failures' / workspace.name
            self.addCleanup(shutil.rmtree, workspace, True)
            self.addCleanup(shutil.rmtree, packet, True)
            self.assertEqual(result.returncode, 23, result.stderr)
            for name in names:
                self.assertEqual((packet / name).read_bytes(),
                                 (name+'\n').encode()+b'\x00raw', name)
            self.assertEqual(json.loads((packet / 'result.json').read_text()), {'exitCode':23})

    def test_unowned_path_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory(prefix='unrelated-workspace-', dir='/tmp') as directory:
            path = Path(directory)
            (path / 'keep').write_text('keep')
            result = self.finish(path, 0)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('refusing to touch', result.stderr)
            self.assertEqual((path / 'keep').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
