#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Deterministic diagnostic failures must never turn receipt admission into success."""

from contextlib import redirect_stderr
import ctypes
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import pid_receipt_diagnostics as diagnostics

spec = importlib.util.spec_from_file_location(
    "helper_lifetime", Path(__file__).with_name("test-lake-helper-lifetime.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class ReceiptDiagnosticsTest(unittest.TestCase):
    def test_complete_receipts_keep_existing_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture.check_pid_receipts(Path(temporary))

    def test_permission_failure_is_not_retried_or_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "helper.pid"
            error = PermissionError(13, "fixture denied", str(path))
            error.winerror = 32
            owner = Mock(pid=1234)
            owner.poll.return_value = None
            with patch.object(Path, "read_text", side_effect=error) as read, \
                    redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(PermissionError) as caught:
                    fixture.entered(root, owner)
            self.assertIs(caught.exception, error)
            self.assertEqual(read.call_count, 1)
            evidence = json.loads((root / "pid-receipt-failure.json").read_text())
            self.assertEqual(evidence["original_error"]["winerror"], 32)
            self.assertEqual(evidence["original_error"]["errno"], 13)
            self.assertEqual(evidence["owner"], {"pid": 1234, "returncode": None})
            self.assertEqual(evidence["phase"], "pid_receipt_read")
            self.assertIn("PID receipt failure observation:", stderr.getvalue())
            if sys.platform != "win32":
                self.assertNotIn("subsequent_native_read_open", evidence)

    def test_diagnostic_fault_cannot_mask_original_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            error = PermissionError(13, "original read failure")
            with patch.object(Path, "read_text", side_effect=error), \
                    patch.object(diagnostics, "path_details", side_effect=RuntimeError("diagnostic")):
                with self.assertRaises(PermissionError) as caught:
                    fixture.pid_receipts(Path(temporary))
            self.assertIs(caught.exception, error)

    def test_evidence_write_failure_still_reports_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(Path, "write_text", side_effect=PermissionError(13, "write")), \
                    redirect_stderr(io.StringIO()) as stderr:
                diagnostics.report_failure(Path(temporary) / "helper.pid", PermissionError(13, "read"))
            self.assertIn('"evidence_write_error"', stderr.getvalue())
            self.assertIn('"strerror": "read"', stderr.getvalue())

    def test_native_probe_records_later_sharing_failure(self):
        kernel = Mock()
        kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True), \
                patch.object(ctypes, "get_last_error", return_value=32, create=True):
            observation = diagnostics.windows_read_open(Path("helper.pid"))
        self.assertFalse(observation["opened"])
        self.assertEqual(observation["winerror"], 32)
        self.assertEqual(kernel.CreateFileW.call_args.args[2], 7)
        kernel.CloseHandle.assert_not_called()

    def test_native_probe_closes_successful_handle(self):
        kernel = Mock()
        kernel.CreateFileW.return_value = 123
        kernel.CloseHandle.return_value = True
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            observation = diagnostics.windows_read_open(Path("helper.pid"))
        self.assertTrue(observation["opened"])
        kernel.CloseHandle.assert_called_once_with(123)

    def test_failed_fixture_is_archived_without_replacing_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "fixture"
            base.mkdir()
            destination = Path(temporary) / "helper-lifetime-failure.tar.gz"
            original = PermissionError(13, "original")

            def fail(root, *unused):
                (root / "helper.pid").write_text("42")
                raise original

            with patch.object(sys, "argv", ["fixture", "--native-prefix", temporary,
                                            "--failure-evidence", str(destination)]), \
                    patch.object(fixture.tempfile, "mkdtemp", return_value=str(base)), \
                    patch.object(fixture, "direct", side_effect=fail), \
                    redirect_stderr(io.StringIO()):
                with self.assertRaises(PermissionError) as caught:
                    fixture.main()
            self.assertIs(caught.exception, original)
            with tarfile.open(destination) as archive:
                self.assertEqual(archive.extractfile("fixture/helper.pid").read(), b"42")
            self.assertTrue(base.exists(), "archiving must preserve the retained workspace")

    def test_archive_failure_cannot_mask_original(self):
        with patch.object(tarfile, "open", side_effect=OSError("archive denied")), \
                redirect_stderr(io.StringIO()) as stderr:
            diagnostics.retain_workspace(Path("fixture"), Path("failure.tar.gz"))
        self.assertIn("archive denied", stderr.getvalue())

    @unittest.skipIf(sys.platform == "win32", "symlink privilege is not part of this local control")
    def test_archive_does_not_follow_outside_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "fixture"
            base.mkdir()
            outside = Path(temporary) / "outside"
            outside.write_text("not fixture evidence")
            (base / "link").symlink_to(outside)
            destination = Path(temporary) / "failure.tar.gz"
            with redirect_stderr(io.StringIO()):
                diagnostics.retain_workspace(base, destination)
            with tarfile.open(destination) as archive:
                self.assertTrue(archive.getmember("fixture/link").issym())
                self.assertEqual(archive.getmember("fixture/link").size, 0)


if __name__ == "__main__":
    unittest.main()
