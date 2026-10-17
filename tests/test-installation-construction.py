#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Validate complete construction records through a non-shipped library fixture."""

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
suffix = ".exe" if os.name == "nt" else ""
with tempfile.TemporaryDirectory(prefix="beam-construction-") as directory:
    subprocess.run([ROOT / ".lake/build/bin" / ("beam-installation-construction-test" + suffix),
                    directory], check=True, timeout=90)
