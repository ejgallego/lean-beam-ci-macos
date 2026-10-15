#!/usr/bin/env python3

# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Check extended-CI destination guards and exact-revision run selection offline."""

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "extended_ci", Path(__file__).resolve().parents[1] / "scripts/extended-ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
SHA = "a" * 40
FORK = ci.DEFAULT_FORK


class ExtendedCiTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.dirty = ""
        self.metadata = {"full_name": FORK, "fork": True,
                         "parent": {"full_name": ci.UPSTREAM}}
        self.variable = "true"
        self.remote_sha = ""
        self.runs = []

    def command(self, *args):
        self.calls.append(args)
        if args[:3] == ("git", "status", "--porcelain"):
            return self.dirty
        if args[:2] == ("git", "rev-parse"):
            return SHA
        if args[:2] == ("gh", "api"):
            path = args[2]
            if path.endswith("/BEAM_FULL_MACOS_CI"):
                return json.dumps({"value": self.variable})
            if path.endswith("/ci.yml"):
                return json.dumps({"state": "active"})
            return json.dumps(self.metadata)
        if args[:2] == ("git", "ls-remote"):
            return f"{self.remote_sha}\trefs/heads/extended-ci/{SHA}" if self.remote_sha else ""
        if args[:3] == ("gh", "run", "list"):
            return json.dumps(self.runs)
        if args[:3] == ("gh", "workflow", "run"):
            self.runs = [{"databaseId": 42, "headSha": SHA, "url": "https://example.test/run/42"}]
        return ""

    def submit(self, **kwargs):
        with patch.object(ci, "run", side_effect=self.command), patch.object(ci.time, "sleep"), \
                patch("builtins.print"):
            return ci.submit(FORK, **kwargs)

    def assert_no_push_or_dispatch(self):
        self.assertFalse(any(call[:2] == ("git", "push") or
                             call[:3] == ("gh", "workflow", "run") for call in self.calls))

    def test_redirect_to_upstream_is_rejected_before_writes(self):
        self.metadata["full_name"] = ci.UPSTREAM
        with self.assertRaisesRegex(ValueError, "redirects"):
            self.submit()
        self.assert_no_push_or_dispatch()

    def test_unrelated_fork_is_rejected(self):
        self.metadata["parent"]["full_name"] = "elsewhere/project"
        with self.assertRaisesRegex(ValueError, "network"):
            self.submit()
        self.assert_no_push_or_dispatch()

    def test_upstream_and_nonfork_destinations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "upstream"):
            ci.verify_fork(ci.UPSTREAM, {"full_name": ci.UPSTREAM, "fork": False})
        self.metadata["fork"] = False
        with self.assertRaisesRegex(ValueError, "upstream"):
            self.submit()
        self.assert_no_push_or_dispatch()

    def test_combined_mode_is_rejected(self):
        self.variable = "false"
        with self.assertRaisesRegex(ValueError, "BEAM_FULL_MACOS_CI"):
            self.submit()
        self.assert_no_push_or_dispatch()

    def test_uncommitted_changes_are_rejected(self):
        self.dirty = " M tracked\n?? new-file"
        with self.assertRaisesRegex(ValueError, "local edits"):
            self.submit()
        self.assert_no_push_or_dispatch()

    def test_conflicting_branch_is_never_overwritten(self):
        self.remote_sha = "b" * 40
        with self.assertRaisesRegex(ValueError, "another commit"):
            self.submit()
        self.assert_no_push_or_dispatch()

    def test_stale_run_cannot_satisfy_current_revision(self):
        self.runs = [{"databaseId": 1, "headSha": "b" * 40, "url": "stale"}]
        self.assertEqual(self.submit()["databaseId"], 42)
        self.assertIn(("git", "push", f"git@github.com:{FORK}.git",
                       f"{SHA}:refs/heads/extended-ci/{SHA}"), self.calls)

    def test_existing_exact_revision_run_is_reused(self):
        self.runs = [{"databaseId": 7, "headSha": SHA, "url": "existing"}]
        self.assertEqual(self.submit()["databaseId"], 7)
        self.assertFalse(any(call[:3] == ("gh", "workflow", "run") for call in self.calls))

    def test_wait_propagates_ci_failure(self):
        original = self.command

        def failing_watch(*args):
            if args[:3] == ("gh", "run", "watch"):
                raise subprocess.CalledProcessError(1, args, stderr="CI failed")
            return original(*args)

        with patch.object(ci, "run", side_effect=failing_watch), patch.object(ci.time, "sleep"), \
                patch("builtins.print"), self.assertRaises(subprocess.CalledProcessError):
            ci.submit(FORK, wait=True)


if __name__ == "__main__":
    unittest.main()
