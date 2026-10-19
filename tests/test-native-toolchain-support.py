#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Exercise native qualification's rejection checks and owned-process cleanup failures."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "native_toolchain", Path(__file__).with_name("test-native-toolchain.py"))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeQualificationSupport(unittest.TestCase):
    def owner(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        self.addCleanup(self.force_cleanup, owner)
        return owner

    @staticmethod
    def force_cleanup(owner):
        try:
            if owner.poll() is None:
                owner.kill()
        finally:
            owner.wait(timeout=5)

    def expired_waits(self, owner, count):
        wait = owner.wait
        timeouts = []

        def injected_wait(*, timeout):
            timeouts.append(timeout)
            if len(timeouts) <= count:
                raise subprocess.TimeoutExpired(owner.args, timeout)
            return wait(timeout=5)

        return mock.patch.object(owner, "wait", side_effect=injected_wait), timeouts

    def test_stop_failures_preserve_the_scenario_error_and_reap_owner(self):
        for stop_error in [subprocess.TimeoutExpired(["beam", "stop"], 10),
                           OSError("stop could not start")]:
            with self.subTest(error=type(stop_error).__name__):
                owner = self.owner()
                wait_patch, timeouts = self.expired_waits(owner, 1)
                original = AssertionError("scenario failed before cleanup")
                diagnostic = io.StringIO()
                command = ["beam", "--root", "project", "--session-dir", "session"]
                env = {"PATH": "fixture runtime"}
                with mock.patch.object(native, "run", side_effect=stop_error) as stop, \
                     wait_patch, contextlib.redirect_stderr(diagnostic):
                    with self.assertRaises(AssertionError) as failure:
                        try:
                            raise original
                        finally:
                            native._cleanup_owner(owner, command, env=env)
                self.assertIs(failure.exception, original)
                self.assertIsNotNone(owner.returncode, "cleanup did not reap its owner")
                stop.assert_called_once_with([*command, "stop"], env=env, ok=False, timeout=10)
                self.assertEqual(timeouts, [10, 10])
                self.assertIn("owner cleanup failed:", diagnostic.getvalue())

    def test_stop_failure_without_a_scenario_error_is_reported_after_reaping(self):
        owner = self.owner()
        wait_patch, _ = self.expired_waits(owner, 1)
        stop_error = OSError("stop could not start")
        with mock.patch.object(native, "run", side_effect=stop_error), wait_patch:
            with self.assertRaises(OSError) as failure:
                native._cleanup_owner(owner, ["beam"], env={})
        self.assertIs(failure.exception, stop_error)
        self.assertIsNotNone(owner.returncode, "cleanup did not reap its owner")

    def test_kill_reaps_an_owner_that_does_not_terminate(self):
        owner = self.owner()
        wait_patch, timeouts = self.expired_waits(owner, 2)
        with mock.patch.object(native, "run"), wait_patch, \
             mock.patch.object(owner, "terminate") as terminate, \
             mock.patch.object(owner, "kill", wraps=owner.kill) as kill:
            native._cleanup_owner(owner, ["beam"], env={})
        terminate.assert_called_once_with()
        kill.assert_called_once_with()
        self.assertEqual(timeouts, [10, 10, 10])
        self.assertIsNotNone(owner.returncode, "cleanup did not reap its owner")

    def test_graceful_stop_reaps_without_kill(self):
        owner = self.owner()
        with mock.patch.object(native, "run", side_effect=lambda *args, **kwargs: owner.terminate()), \
             mock.patch.object(owner, "kill", wraps=owner.kill) as kill:
            native._cleanup_owner(owner, ["beam"], env={})
        kill.assert_not_called()
        self.assertIsNotNone(owner.returncode, "cleanup did not reap its owner")

    def test_terminate_error_still_kills_and_reaps(self):
        owner = self.owner()
        wait_patch, _ = self.expired_waits(owner, 2)
        terminate_error = OSError("terminate failed")
        with mock.patch.object(native, "run"), wait_patch, \
             mock.patch.object(owner, "terminate", side_effect=terminate_error), \
             mock.patch.object(owner, "kill", wraps=owner.kill) as kill:
            with self.assertRaises(OSError) as failure:
                native._cleanup_owner(owner, ["beam"], env={})
        self.assertIs(failure.exception, terminate_error)
        kill.assert_called_once_with()
        self.assertIsNotNone(owner.returncode, "cleanup did not reap its owner")

    def test_exited_owner_needs_no_stop(self):
        owner = self.owner()
        owner.terminate()
        owner.wait(timeout=5)
        with mock.patch.object(native, "run") as stop:
            native._cleanup_owner(owner, ["beam"], env={})
        stop.assert_not_called()

    def test_dacl_rejection_requires_its_diagnostic_and_failure_exit(self):
        diagnostic = ("unsafe session directory C:\\fixture\\unsafe session directory: "
                      "the Windows DACL must use ordinary allow entries, grant full access "
                      "to the current user, and grant no access to other ordinary accounts")
        native._assert_dacl_rejection(1, "", diagnostic)
        for code, stderr in [
            (0, diagnostic),
            (1, "failed to open C:\\fixture\\unsafe session directory: sharing violation"),
            (1, "unsafe session directory: the path is not a directory"),
        ]:
            with self.subTest(code=code, stderr=stderr), self.assertRaises(AssertionError):
                native._assert_dacl_rejection(code, "", stderr)


if __name__ == "__main__":
    unittest.main()
