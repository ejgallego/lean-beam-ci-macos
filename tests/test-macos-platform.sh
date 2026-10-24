#!/usr/bin/env bash

# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

# Standalone checks supplementing native qualification in the macOS PR profile. Native
# qualification owns the portable process, filesystem, manifest, staging, and lookup checks.
set -euo pipefail
cd "$(dirname "$0")/.."
. tests/lib/ci-steps.sh
BEAM_TEST_SUITE=macos-platform
platform_state_root="$(mktemp -d /tmp/beam-macos-platform-XXXXXX)"
trap 'rm -rf -- "$platform_state_root"' EXIT
export BEAM_SESSION_ROOT="$platform_state_root/sessions"

run_step "build platform fixtures" lake build \
  Beam.LSP:shared beam-cli beam-daemon lean-beam-mcp beam-install-runtime \
  beam-lake-helper-client-fixture beam-cli-daemon-test beam-installation-stage-fixture

run_step "CLI session lifecycle" python3 tests/lib/process_watchdog.py \
  --timeout 90 -- .lake/build/bin/beam-cli-daemon-test
run_step "installed entrypoints" python3 tests/test-installation-entrypoints.py
# Slow already runs the focused shell installation publication checks. Prune is otherwise
# owned by the exhaustive installer suite, so keep its complete guard cases in this PR profile.
run_step "installed prune guards" bash tests/test-beam-prune.sh
