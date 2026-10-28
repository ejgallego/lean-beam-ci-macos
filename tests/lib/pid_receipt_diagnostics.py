# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Failure-only observations for the owned Lake helper fixture; never a readiness retry."""

import json
import os
import sys
import tarfile
import time


def error_details(error):
    return {"type": type(error).__name__, **{
        name: getattr(error, name, None)
        for name in ("errno", "winerror", "strerror", "filename", "filename2")}}


def path_details(path):
    try:
        info = path.lstat()
        return {name: getattr(info, name) for name in
                ("st_mode", "st_size", "st_dev", "st_ino", "st_nlink", "st_mtime_ns")}
    except OSError as error:
        return {"error": error_details(error)}


def windows_read_open(path):
    # This is a separately timed observation AFTER the Python read failed. Success does not
    # establish what blocked that read, and must never allow a lifetime assertion to proceed.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    started = time.monotonic_ns()
    # GENERIC_READ, FILE_SHARE_READ|WRITE|DELETE, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL.
    handle = create(str(path), 0x80000000, 7, None, 3, 0x80, None)
    result = {"monotonic_ns": started, "sharing": "read|write|delete"}
    if handle == ctypes.c_void_p(-1).value:
        result["winerror"] = ctypes.get_last_error()
        result["opened"] = False
    else:
        result["opened"] = True
        if not close(handle):
            result["close_winerror"] = ctypes.get_last_error()
    return result


def report_failure(path, error, owner=None):
    """Best-effort evidence; all failures here leave the original read exception intact."""
    try:
        observation = {"phase": "pid_receipt_read", "monotonic_ns": time.monotonic_ns(),
                       "path": str(path), "original_error": error_details(error),
                       "owner": None if owner is None else
                       {"pid": owner.pid, "returncode": owner.poll()},
                       "receipt": path_details(path),
                       "unpublished_receipt": path_details(path.with_suffix(".pid.tmp"))}
        if os.name == "nt":
            try:
                observation["subsequent_native_read_open"] = windows_read_open(path)
            except Exception as probe_error:
                observation["native_probe_error"] = error_details(probe_error)
        evidence = path.parent / "pid-receipt-failure.json"
        try:
            evidence.write_text(json.dumps(observation, indent=2) + "\n", encoding="utf-8")
        except OSError as write_error:
            observation["evidence_write_error"] = error_details(write_error)
        print("PID receipt failure observation:", json.dumps(observation), file=sys.stderr,
              flush=True)
    except Exception:
        # Diagnostics must not replace an exception even if stderr, owner.poll, or JSON fails.
        pass


def retain_workspace(base, destination):
    """Archive the owned fixture after failure unwinding; this is not an atomic snapshot."""
    try:
        with tarfile.open(destination, "x:gz", dereference=False) as archive:
            archive.add(base, arcname=base.name)
        print("Failed helper workspace archive:", destination, file=sys.stderr, flush=True)
    except Exception as error:
        try:
            print("Failed helper workspace archive could not be retained:", error,
                  file=sys.stderr, flush=True)
        except Exception:
            pass
