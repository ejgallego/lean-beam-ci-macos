#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Check exit polling boundaries without real PIDs or elapsed-time races."""

from pathlib import Path
import shlex
import subprocess
import unittest

WAIT = Path(__file__).resolve().parent / "lib/wait.sh"


class WaitForExitTest(unittest.TestCase):
    def run_shell(self, body):
        return subprocess.run(["bash", "-eu", "-c", f". {shlex.quote(str(WAIT))}\n{body}"],
                              capture_output=True, text=True, timeout=10)

    def test_already_exited_does_not_sleep(self):
        result = self.run_shell("""
kill() { return 1; }
sleep() { exit 88; }
wait_for_exit 123 fixture 2 0.05
""")
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(result.stderr, "")

    def test_exit_during_final_sleep_is_observed(self):
        result = self.run_shell("""
running=1
checks=0
sleeps=0
kill() { checks=$((checks + 1)); [ "$running" -eq 1 ]; }
sleep() { sleeps=$((sleeps + 1)); running=0; }
wait_for_exit 123 fixture 1 0.05
printf 'checks=%s sleeps=%s\\n' "$checks" "$sleeps"
""")
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(result.stdout, "checks=2 sleeps=1\n")
        self.assertEqual(result.stderr, "")

    def test_live_process_still_times_out(self):
        result = self.run_shell("""
checks=0
sleeps=0
kill() { checks=$((checks + 1)); return 0; }
sleep() { sleeps=$((sleeps + 1)); }
if wait_for_exit 123 fixture 2 0.05; then exit 99; else status=$?; fi
printf 'status=%s checks=%s sleeps=%s\\n' "$status" "$checks" "$sleeps"
""")
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(result.stdout, "status=1 checks=3 sleeps=2\n")
        self.assertIn("timed out waiting for fixture (pid 123) to exit", result.stderr)


if __name__ == "__main__":
    unittest.main()
