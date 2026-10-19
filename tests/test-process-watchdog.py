#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Exercise the watchdog through real child processes, including inherited output."""

import csv
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


HELPER = Path(__file__).parent / "lib" / "process_watchdog.py"
spec = importlib.util.spec_from_file_location("process_watchdog", HELPER)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def process_is_running(pid):
    # A POSIX zombie has terminated even if its new parent has not reaped it yet.
    if os.name == "posix":
        result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                capture_output=True, text=True, timeout=5, check=False)
        if result.returncode not in (0, 1):
            raise RuntimeError(f"ps failed: {result.stderr}")
        return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")
    result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, timeout=5, check=True)
    return any(len(row) > 1 and row[1] == str(pid)
               for row in csv.reader(result.stdout.splitlines()))


def kill_fixture_process(pid):
    # Call only with the fresh PID recorded by an owned fixture.
    if os.name == "posix":
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False)


class ProcessWatchdog(unittest.TestCase):
    def run_python(self, code, timeout=5):
        return watchdog.run_with_watchdog([sys.executable, "-c", code], timeout=timeout)

    def test_success_keeps_stdout_and_diagnostic_stderr(self):
        result = self.run_python("import sys; print('ok'); print('diagnostic', file=sys.stderr)")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(result.stderr, "diagnostic\n")

    def test_nonzero_exit_is_preserved(self):
        result = self.run_python("import sys; print('failure', file=sys.stderr); sys.exit(7)")
        self.assertEqual(result.returncode, 7)
        self.assertIn("failure", result.stderr)

    def test_output_capture_is_bounded(self):
        result = self.run_python("print('x' * (2 * 1024 * 1024)); print('retained tail')")
        self.assertLess(len(result.stdout), 1024 * 1024 + 100)
        self.assertTrue(result.stdout.endswith("retained tail\n"))

    def test_success_does_not_wait_for_inherited_output_eof(self):
        with tempfile.TemporaryDirectory(prefix="beam-watchdog-inherited-") as directory:
            pid_file = Path(directory) / "holder.pid"
            code = f"""
import subprocess, sys
from pathlib import Path
holder = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path({str(pid_file)!r}).write_text(str(holder.pid))
print('completed with inherited output')
"""
            try:
                result = self.run_python(code, timeout=2)
                self.assertEqual(result.returncode, 0)
                self.assertIn("completed with inherited output", result.stdout)
            finally:
                if pid_file.exists():
                    kill_fixture_process(int(pid_file.read_text()))

    def test_timeout_covers_inherited_pipes_and_partial_output(self):
        # Supervise the watchdog itself; files keep its descendants out of communicate().
        with tempfile.TemporaryDirectory(prefix="beam-watchdog-test-") as directory:
            root = Path(directory)
            pid_file = root / "descendant.pid"
            code = f"""
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path({str(pid_file)!r}).write_text(str(child.pid))
print('partial response', end='', flush=True)
print('diagnostic before stall', file=sys.stderr, flush=True)
time.sleep(60)
"""
            script = root / "child.py"
            script.write_text(code)
            try:
                with (root / "out").open("w+b") as out, (root / "err").open("w+b") as err:
                    result = subprocess.run([sys.executable, str(HELPER), "--timeout", "2", "--",
                                             sys.executable, str(script)],
                                            stdout=out, stderr=err, timeout=15)
                    out.seek(0)
                    err.seek(0)
                    self.assertEqual(result.returncode, 124)
                    self.assertIn(b"partial response", out.read())
                    stderr = err.read()
                    self.assertIn(b"diagnostic before stall", stderr)
                    self.assertIn(b"external watchdog expired after 2 seconds", stderr)
                    self.assertNotIn(b"watchdog cleanup failed", stderr)
                pid = int(pid_file.read_text())
                deadline = time.monotonic() + 2
                while process_is_running(pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(process_is_running(pid), "watchdog left its descendant running")
            finally:
                if pid_file.exists():
                    kill_fixture_process(int(pid_file.read_text()))

    def test_invalid_timeout_does_not_launch(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.run_python("raise AssertionError('must not run')", timeout)

    def test_owned_scope_reaps_the_tree_and_preserves_the_action_failure(self):
        for failure in [None, AssertionError("scenario failed"), OSError("stop could not start"),
                        subprocess.TimeoutExpired(["fixture", "stop"], 10)]:
            with self.subTest(failure=type(failure).__name__), \
                 tempfile.TemporaryDirectory(prefix="beam-owned-tree-") as directory:
                pid_file = Path(directory) / "child.pid"
                code = f"""
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
marker = Path({str(pid_file)!r})
pending = marker.with_suffix('.tmp')
pending.write_text(str(child.pid))
pending.replace(marker)
time.sleep(60)
"""
                proc = None
                try:
                    expectation = self.assertRaises(type(failure)) if failure else contextlib.nullcontext()
                    with expectation as raised:
                        with watchdog.owned_process([sys.executable, "-c", code],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
                            deadline = time.monotonic() + 5
                            while not pid_file.exists():
                                self.assertIsNone(proc.poll(), "fixture exited before starting its child")
                                self.assertLess(time.monotonic(), deadline, "fixture did not start its child")
                                time.sleep(0.01)
                            if failure:
                                raise failure
                    if failure:
                        self.assertIs(raised.exception, failure)
                    self.assertIsNotNone(proc.returncode, "owned scope did not reap its process")
                    child = int(pid_file.read_text())
                    deadline = time.monotonic() + 2
                    while process_is_running(child) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertFalse(process_is_running(child), "owned scope left its descendant running")
                finally:
                    if proc is not None and proc.poll() is None:
                        watchdog._terminate_tree(proc)
                    if pid_file.exists():
                        kill_fixture_process(int(pid_file.read_text()))

    def test_owned_scope_reports_cleanup_failure_without_replacing_an_action_failure(self):
        terminate = watchdog._terminate_tree
        for failure in [None, AssertionError("scenario failed")]:
            for cleanup in [OSError("injected cleanup failure"),
                            subprocess.TimeoutExpired(["fixture", "cleanup"], 5)]:
                with self.subTest(failure=type(failure).__name__, cleanup=type(cleanup).__name__):
                    def fail_after_reaping(proc):
                        terminate(proc)
                        raise cleanup

                    proc = None
                    try:
                        diagnostic = io.StringIO()
                        with mock.patch.object(watchdog, "_terminate_tree", side_effect=fail_after_reaping), \
                             contextlib.redirect_stderr(diagnostic), \
                             self.assertRaises(type(failure or cleanup)) as raised:
                            with watchdog.owned_process(
                                    [sys.executable, "-c", "import time; time.sleep(60)"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
                                if failure:
                                    raise failure
                        self.assertIs(raised.exception, failure or cleanup)
                        self.assertIsNotNone(proc.returncode)
                        if failure:
                            self.assertIn("watchdog cleanup failed:", diagnostic.getvalue())
                    finally:
                        if proc is not None and proc.poll() is None:
                            terminate(proc)

    def test_owned_scope_keeps_an_already_exited_process_status(self):
        with watchdog.owned_process([sys.executable, "-c", "import sys; sys.exit(7)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
            self.assertEqual(proc.wait(timeout=5), 7)
        self.assertEqual(proc.returncode, 7)


if __name__ == "__main__":
    unittest.main()
