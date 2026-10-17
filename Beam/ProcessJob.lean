/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Lean

namespace Beam.ProcessJob

@[extern "lean_beam_process_job_attach"]
private opaque attach (pid : UInt32) : IO USize

@[extern "lean_beam_process_job_close"]
private opaque close (job : USize) : IO Unit

@[extern "lean_beam_process_job_poll"]
private opaque pollNative (pid : UInt32) : IO UInt8

@[extern "lean_beam_process_job_terminate_posix"]
private opaque terminatePosix (pid : UInt32) : IO Unit

inductive State where
  | running
  | exited
  | notChild

/-- Observe without reaping. The caller must retain its unreaped Child throughout this scope. -/
def poll (child : IO.Process.Child cfg) : IO State := do
  match ← pollNative child.pid with
  | 0 => pure .running
  | 1 => pure .exited
  | 2 => pure .notChild
  | _ => throw <| IO.userError "invalid native process-job state"

/-- Attach before allowing project code to run; the retained Child prevents Windows PID reuse. -/
def withChild (child : IO.Process.Child cfg) (action : IO α) : IO α := do
  let job ← attach child.pid
  try action finally close job

/-- Terminate the retained leader and its POSIX group before the caller's one reap. -/
def terminate (child : IO.Process.Child cfg) : IO Unit := do
  if System.Platform.isWindows then
    try child.kill
    catch error =>
      -- Job closure or natural exit may have terminated the leader already. Confirm exit
      -- through its retained handle before accepting a failed TerminateProcess. Windows
      -- tryWait does not reap; POSIX must retain its unreaped child until group cleanup.
      if (← child.tryWait).isNone then throw error
  else
    terminatePosix child.pid

/-- Child-side owner-pipe EOF. POSIX group termination is guarded by session-leader ownership. -/
@[extern "lean_beam_process_job_parent_lost"]
opaque parentLost : IO Unit

end Beam.ProcessJob
