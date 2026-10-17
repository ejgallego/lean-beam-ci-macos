#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Local target lookup never executes candidate compilers, providers, or construction tools."""

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
suffix = ".exe" if os.name == "nt" else ""
with tempfile.TemporaryDirectory(prefix="beam-installation-lookup-") as temporary:
    directory = Path(temporary).resolve()
    tools = directory / "elan/toolchains"
    tools.mkdir(parents=True)
    calls = directory / "foreign-invocations"
    for name in ["sdk-0", "sdk-1", "foreign"]:
        command = directory / name / "bin" / ("lean" + suffix)
        command.parent.mkdir(parents=True)
        command.write_text(f'#!/bin/sh\nprintf invoked >> "{calls}"\nexit 99\n')
        command.chmod(0o755)
    for alias, target in [("fixture-alias", "sdk-0"), ("fixture-second", "sdk-1"),
                          ("foreign-alias", "foreign"),
                          ("leanprover--lean4---v4.33.1", "sdk-0")]:
        (tools / alias).symlink_to(directory / target, target_is_directory=True)
    (directory / "project").mkdir()
    environment = dict(os.environ, ELAN_HOME=str(directory / "elan"),
                       BEAM_HOME=str(directory / "foreign"),
                       BEAM_BUNDLE_DIR=str(directory / "forbidden-build"),
                       BEAM_INSTALL_BUNDLE_DIR=str(directory / "foreign-cache"))
    subprocess.run([ROOT / ".lake/build/bin" / ("beam-installation-lookup-test" + suffix), directory],
                   env=environment, check=True, timeout=90)
    assert not calls.exists(), "lookup invoked a candidate compiler before rejecting it"
    assert not (directory / "forbidden-build").exists(), "lookup attempted runtime construction"
    assert not (directory / "foreign-cache").exists(), "lookup searched or created a foreign provider cache"
