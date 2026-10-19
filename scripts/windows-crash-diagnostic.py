#!/usr/bin/env python3
"""Temporary fork-only debugger experiment for PR 277's Windows client crash."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / '.native-debug'
LOGS.mkdir(exist_ok=True)
GDB = Path(shutil.which('gdb')).resolve()
spec = importlib.util.spec_from_file_location('native_qualification', ROOT / 'tests/test-native-toolchain.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
original_run = native.run
original_owner = native.running_owner
sequence = 0


def debug_run(args, *, cwd=ROOT, env=None, ok=True, timeout=600, force=False):
    global sequence
    if not force and (Path(args[0]).name != 'lean-beam.exe' or '--root' not in args):
        return original_run(args, cwd=cwd, env=env, ok=ok, timeout=timeout)
    sequence += 1
    prefix = LOGS / f'client-{sequence:04}'
    stdout = prefix.with_suffix('.stdout')
    stderr = prefix.with_suffix('.stderr')
    command = subprocess.list2cmdline([a.as_posix() if isinstance(a, Path) else str(a) for a in args[1:]])
    command += f' > "{stdout.as_posix()}" 2> "{stderr.as_posix()}"'
    script = prefix.with_suffix('.gdb')
    script.write_text('\n'.join([
        'set pagination off', 'set confirm off', 'set print thread-events off', 'set host-charset UTF-8',
        'set breakpoint pending on', 'break __stack_chk_fail',
        'run ' + command,
        'python print("BEAM_DIAG_EXIT", gdb.parse_and_eval("$_exitcode"))',
        'thread apply all bt 40', 'info sharedlibrary', 'info registers',
        'x/24i $pc-32', 'quit',
    ]) + '\n', encoding='utf-8')
    print(f'debug client {sequence}: {args}', flush=True)
    result = subprocess.run([GDB, '-nx', '-q', '-batch', '-x', script,
                             '--args', args[0]], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=min(timeout, 120))
    prefix.with_suffix('.debugger.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    match = re.search(r'BEAM_DIAG_EXIT (\d+)', result.stdout)
    if not match:
        print(result.stdout + result.stderr, flush=True)
        raise AssertionError(f'client stopped before exit; stack saved in {prefix}')
    completed = subprocess.CompletedProcess(args, int(match[1]),
        stdout.read_text(encoding='utf-8', errors='replace'),
        stderr.read_text(encoding='utf-8', errors='replace'))
    if ok and completed.returncode:
        raise AssertionError(f'{args}: {completed.returncode}\n{completed.stdout}\n{completed.stderr}')
    return completed


@contextmanager
def stress_owner(beam, project, env, log_path):
    global stressed
    with original_owner(beam, project, env, log_path) as owner:
        # Stress short-lived TCP/signal clients independently of document elaboration.
        for _ in range(128 if not stressed else 0):
            result = debug_run([beam, '--root', project, 'stats'], env=env, timeout=30)
            assert json.loads(result.stdout)['ok'], result
        stressed = True
        yield owner


# Check the exact debugger process/Unicode transport before doing expensive construction.
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
probe_source = LOGS / 'transport λ.lean'
probe_source.write_text('def main : IO Unit := IO.println "debugger transport λ"\n', encoding='utf-8')
lean = Path(subprocess.check_output(['elan', 'which', 'lean'], text=True).strip())
probe = debug_run([lean, '--run', probe_source], force=True)
assert probe.stdout.strip() == 'debugger transport λ', probe
print('debugger Unicode process transport passed', flush=True)
stressed = False
native.run = debug_run
native.running_owner = stress_owner
native.main()
