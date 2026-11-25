#!/usr/bin/env python3
"""Run the bounded standalone Windows job/handle-list comparison; retain every outcome."""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--markers-only", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("requires a native Windows runner")
    args.output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("windows-owned-launch-probe.c")
    report = {
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"],
                                                     text=True).strip()),
        "probe_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "executable_sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest(),
        "platform": platform.platform(), "python": sys.version,
        "runner_image": {key: os.environ.get(key) for key in ["ImageOS", "ImageVersion",
                                                              "RUNNER_OS", "RUNNER_ARCH"]},
        "run_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                   f"{os.environ['GITHUB_RUN_ID']}",
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "scope": "standalone Win32 experiment; no Beam/Lean build or production qualification",
        "guardian": "each coordinator retains a non-inherited strict kill-on-close safety job",
        "probe_kind": "job-marker" if args.markers_only else "owned-launch",
        "cases": [], "result": "running",
    }
    destination = args.output / "result.json"
    contexts = ["hosted", "silent-breakaway", "nested-nonbreakaway"]
    modes = ["job-list", "suspended-assign"]
    cases = ["normal", "owner-death", "assignment-failure", "preassign-owner-death"]
    matrix = list(itertools.product(contexts, modes, cases))
    matrix += [("hosted", mode, "handle-list-control") for mode in modes]
    if args.markers_only:
        matrix = list(itertools.product(contexts, modes, [
            "marker-force", "marker-exit-23", "marker-exit-equal",
            "marker-owner-death", "marker-last-close"]))
    try:
        if report["source_dirty"]:
            raise RuntimeError("probe source checkout is dirty")
        for context, mode, case in matrix:
            label = f"{context}-{mode}-{case}"
            observation = {"context": context, "mode": mode, "case": case}
            try:
                result = subprocess.run([str(args.executable.resolve()), context, mode, case],
                                        capture_output=True, timeout=25)
                (args.output / f"{label}.stdout").write_bytes(result.stdout)
                (args.output / f"{label}.stderr").write_bytes(result.stderr)
                observation["returncode"] = result.returncode
                if result.returncode:
                    observation["result"] = "failed"
                else:
                    observation.update(json.loads(result.stdout))
                    if (observation["context"], observation["mode"], observation["case"]) != (
                            context, mode, case) or observation["result"] != "passed":
                        raise RuntimeError("native case identity/result mismatch")
            except subprocess.TimeoutExpired as error:
                observation.update(result="timeout", timeout_seconds=25)
                (args.output / f"{label}.stdout").write_bytes(error.stdout or b"")
                (args.output / f"{label}.stderr").write_bytes(error.stderr or b"")
            report["cases"].append(observation)
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(observation), flush=True)
        report["result"] = "passed" if all(c["result"] == "passed" for c in report["cases"]) else "failed"
    except Exception as error:
        report.update(result="failed", failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
