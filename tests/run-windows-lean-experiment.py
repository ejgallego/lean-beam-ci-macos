#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Execute the admitted SDK, the Lean-only reduction, and existing native qualification."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import threading
import traceback

from lib.process_watchdog import run_with_watchdog


REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "tests/windows_uv_project"
MINIMAL_SHA256 = "18311bc7262be62d0543eba25f8d36607d739e681b50e09ff9a297955ea073ec"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(command, *, cwd, env, log, timeout):
    try:
        result = run_with_watchdog(command, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        streams = [value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
                   for value in [error.stdout, error.stderr]]
        log.write_text("".join(streams), encoding="utf-8")
        raise
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    require(result.returncode == 0, f"command exited {result.returncode}; see {log}")
    return result.stdout


def ping_pong(listener, stop, errors):
    while not stop.is_set():
        try:
            connection, _ = listener.accept()
        except socket.timeout:
            continue
        except OSError as error:
            if not stop.is_set():
                errors.append(str(error))
            return
        with connection:
            connection.settimeout(5)
            try:
                data = b""
                while len(data) < 4:
                    chunk = connection.recv(4 - len(data))
                    require(bool(chunk), "Lean-only peer closed before ping")
                    data += chunk
                require(data == b"ping", "Lean-only peer sent an unexpected request")
                connection.sendall(b"pong")
            except (OSError, ValueError) as error:
                errors.append(str(error))


def unpack_sdk(tar, archive, destination, *, env, logs):
    # Native Python does not perform MSYS path conversion. Convert BOTH file operands with
    # cygpath from the same MSYS installation as tar; keep each spaced path one argument.
    paths = run([tar.with_name("cygpath.exe"), "-u", archive, destination],
                cwd=REPO, env=env, log=logs / "unpack-paths.log", timeout=30).splitlines()
    require(len(paths) == 2 and all(path.startswith("/") for path in paths),
            "cygpath did not return two absolute MSYS paths")
    run([tar, "--force-local", "--zstd", "-xf", paths[0], "-C", paths[1]],
        cwd=REPO, env=env, log=logs / "unpack.log", timeout=300)


def experiment(args, report):
    require(os.name == "nt", "the experimental consumer requires a Windows runner")
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    require(selection["result"] == "downloaded", "SDK artifact was not admitted and downloaded")
    report.update(producer=selection, beam_sha=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(), platform=platform.platform())
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    sdk_root = scratch / "sdk"
    sdk_root.mkdir()
    report["stage"] = "unpack-sdk"
    with Path(selection["archive_path"]).open("rb") as source:
        require(hashlib.file_digest(source, "sha256").hexdigest() == selection["archive_sha256"],
                "admitted SDK tarball changed before extraction")
    unpack_sdk(args.tar, selection["archive_path"], sdk_root, env=os.environ.copy(), logs=scratch)
    prefix = sdk_root / selection["archive_name"].removesuffix(".tar.zst")
    lean, lake = prefix / "bin/lean.exe", prefix / "bin/lake.exe"
    require(lean.is_file() and lake.is_file(), "archive did not contain the expected Windows SDK root")
    runtime_env = dict(os.environ, PATH=str(prefix / "bin") + os.pathsep
                       + str(Path(os.environ["SystemRoot"]) / "System32"), LEAN_SYSROOT=str(prefix))
    for name in ["LEAN_PATH", "LEAN_SRC_PATH", "LAKE_HOME"]:
        runtime_env.pop(name, None)
    report["stage"] = "runtime-identity"
    report["lean_version"] = run([lean, "--version"], cwd=scratch, env=runtime_env,
                                 log=scratch / "lean-version.log", timeout=30).strip()
    report["lean_sha"] = run([lean, "--githash"], cwd=scratch, env=runtime_env,
                             log=scratch / "lean-sha.log", timeout=30).strip()
    require(report["lean_sha"] == selection["lean_sha"], "extracted SDK source SHA does not match")
    report["lake_version"] = run([lake, "--version"], cwd=scratch, env=runtime_env,
                                 log=scratch / "lake-version.log", timeout=30).strip()
    project = scratch / "Lean-only project with spaces"
    project.mkdir()
    for name in ["MinimalClient.lean", "LibuvVersion.lean", "lakefile.toml"]:
        shutil.copy2(PROBE / name, project / name)
    require(hashlib.sha256((project / "MinimalClient.lean").read_bytes()).hexdigest() == MINIMAL_SHA256,
            "the preserved 31-line Lean reduction changed")
    report["minimal_source_sha256"] = MINIMAL_SHA256
    run(["elan", "toolchain", "link", "beam-libuv-experiment", prefix], cwd=REPO,
        env=os.environ.copy(), log=scratch / "elan-link.log", timeout=30)
    (project / "lean-toolchain").write_text("beam-libuv-experiment\n", encoding="utf-8")
    build_env = dict(os.environ, LEAN_SYSROOT=str(prefix), LEAN=str(lean), LAKE_CACHE_DIR="",
                     ELAN_TOOLCHAIN="beam-libuv-experiment")
    for name in ["LEAN_PATH", "LEAN_SRC_PATH", "LAKE_HOME"]:
        build_env.pop(name, None)
    report["stage"] = "build-lean-only-probe"
    run([lake, "--no-cache", "build", "uv-client", "libuv-version"], cwd=project, env=build_env,
        log=scratch / "probe-build.log", timeout=300)
    interpreted = json.loads(run([lean, "--run", "LibuvVersion.lean"], cwd=project, env=runtime_env,
                                 log=scratch / "libuv-interpreted.log", timeout=30))
    compiled = json.loads(run([project / ".lake/build/bin/libuv-version.exe"], cwd=project,
                              env=runtime_env, log=scratch / "libuv-compiled.log", timeout=30))
    require(interpreted == compiled and type(compiled["packed"]) is int
            and compiled["packed"] >= 0x013500, "compiled/interpreted libuv identity did not agree")
    report.update(libuv_interpreted=interpreted, libuv_compiled=compiled)
    report["stage"] = "lean-only-tcp-signal-probe"
    stop, errors, results = threading.Event(), [], []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)
    server = threading.Thread(target=ping_pong, args=(listener, stop, errors))
    server.start()
    try:
        for iteration in range(256):
            report["lean_only_current_iteration"] = iteration
            child = subprocess.run([str(project / ".lake/build/bin/uv-client.exe"),
                                    str(listener.getsockname()[1])], cwd=project, env=runtime_env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=30)
            results.append({"iteration": iteration, "returncode": child.returncode,
                            "stdout": child.stdout, "stderr": child.stderr})
            require(child.returncode == 0, f"Lean-only reduction failed at iteration {iteration}")
    finally:
        stop.set()
        listener.close()
        server.join(timeout=6)
        (scratch / "lean-only-results.json").write_text(
            json.dumps({"results": results, "server_errors": errors,
                        "server_settled": not server.is_alive()}, indent=2) + "\n", encoding="utf-8")
    require(not server.is_alive() and not errors, "Lean-only peer did not settle cleanly")
    report["lean_only_executions"] = len(results)
    report["stage"] = "beam-native-qualification"
    run([sys.executable, REPO / "tests/test-native-toolchain.py", prefix, "--crlf",
         "--native-system-include-dir", args.native_include], cwd=REPO, env=build_env,
        log=scratch / "beam-native.log", timeout=1800)
    report["result"] = "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--tar", required=True, type=Path)
    parser.add_argument("--native-include", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    report = {"result": "running", "stage": "admission"}
    try:
        experiment(args, report)
    except Exception as error:
        report.update(result="failed", failure=traceback.format_exc())
        if isinstance(error, subprocess.TimeoutExpired):
            for name in ["stdout", "stderr"]:
                value = getattr(error, name)
                if value is not None:
                    report["timeout_" + name] = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        raise
    finally:
        target = args.selection.with_name("experiment.json")
        try:
            target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            print(f"Could not retain Windows experiment report: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
