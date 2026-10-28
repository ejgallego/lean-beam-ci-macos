#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Native Python must convert archive operands at the MSYS tar boundary."""

import importlib.util
from pathlib import Path, PureWindowsPath
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "windows_experiment", Path(__file__).with_name("run-windows-lean-experiment.py"))
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class ExtractionTest(unittest.TestCase):
    def setUp(self):
        self.tar = Path("MSYS installation with spaces/usr/bin/tar.exe")
        self.archive = PureWindowsPath(r"D:\artifact with spaces\toolchain.tar.zst")
        self.destination = PureWindowsPath(r"D:\runtime with spaces\sdk")
        self.logs = Path("logs")

    def extract(self):
        experiment.unpack_sdk(self.tar, str(self.archive), self.destination,
                              env={"PATH": "fixture"}, logs=self.logs)

    def test_both_native_operands_are_converted_by_the_selected_msys(self):
        with patch.object(experiment, "run", side_effect=[
                "/d/artifact with spaces/toolchain.tar.zst\n/d/runtime with spaces/sdk\n", ""]) as run:
            self.extract()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0],
                         [self.tar.with_name("cygpath.exe"), "-u", str(self.archive), self.destination])
        self.assertEqual(run.call_args_list[1].args[0],
                         [self.tar, "--force-local", "--zstd", "-xf",
                          "/d/artifact with spaces/toolchain.tar.zst", "-C", "/d/runtime with spaces/sdk"])
        self.assertEqual(run.call_args_list[0].kwargs["log"], self.logs / "unpack-paths.log")

    def test_missing_or_native_conversion_results_stop_before_tar(self):
        for output in ["", "/d/archive\n", "/d/archive\nD:\\sdk\n"]:
            with self.subTest(output=output), patch.object(experiment, "run", return_value=output) as run:
                with self.assertRaisesRegex(ValueError, "two absolute MSYS paths"):
                    self.extract()
                self.assertEqual(run.call_count, 1)

    def test_conversion_or_extraction_failure_is_not_retried(self):
        for results, attempts in [([ValueError("conversion failed")], 1),
                                  (["/d/archive\n/d/sdk\n", ValueError("tar failed")], 2)]:
            with self.subTest(attempts=attempts), patch.object(experiment, "run", side_effect=results) as run:
                with self.assertRaises(ValueError) as caught:
                    self.extract()
                self.assertIs(caught.exception, results[-1])
                self.assertEqual(run.call_count, attempts)


if __name__ == "__main__":
    unittest.main()
