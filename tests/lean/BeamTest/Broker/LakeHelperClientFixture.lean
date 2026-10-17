/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Beam.Broker.LakeHelper

open Lean Beam.Broker

private def awaitExited (child : IO.Process.Child cfg) : IO Unit := do
  let deadline := (← IO.monoMsNow) + 5000
  repeat
    match ← Beam.ProcessJob.poll child with
    | .exited => return
    | .notChild => throw <| IO.userError "fixture lost its unreaped child"
    | .running =>
        if (← IO.monoMsNow) >= deadline then
          throw <| IO.userError "fixture child did not exit"
        IO.sleep 10

/-- Keep an exited session leader unreaped: its remaining group may contain only zombies. -/
private def checkExitedGroups : IO Unit := do
  for _ in [:16] do
    let child ← IO.Process.spawn {
      cmd := (← IO.appPath).toString
      args := #["--process-job-exit"]
      stdin := .null, stdout := .null, stderr := .null
      setsid := true
    }
    try
      awaitExited child
      Beam.ProcessJob.terminate child
    finally
      discard <| child.wait
  IO.println "unreaped exited process groups passed"

private def holdForJobDiagnostic : IO Unit := do
  let workers ← (List.range 8).mapM fun _ =>
    IO.asTask (prio := Task.Priority.dedicated) (IO.sleep 10000)
  let stdout ← IO.getStdout
  stdout.putStrLn "ready"
  stdout.flush
  for worker in workers do discard <| IO.wait worker

private def checkJobCloseDiagnostic : IO Unit := do
  for useJob in [false, true] do
    let count := if useJob then 64 else 8
    for iteration in [:count] do
      let child ← IO.Process.spawn {
        cmd := (← IO.appPath).toString
        args := #["--process-job-hold-diagnostic"]
        stdin := .piped, stdout := .piped, stderr := .null
        setsid := true
      }
      let pid := child.pid
      let ready : IO Unit := do
        unless (← child.stdout.getLine).trimAscii.toString == "ready" do
          throw <| IO.userError "diagnostic child did not report ready"
      let scopeError? ← try
        if useJob then Beam.ProcessJob.withChild child ready else ready
        pure (none : Option String)
      catch error => pure (some error.toString)
      -- No printing or extra handle observation between job closure and production cleanup.
      let terminateError? ← try
        Beam.ProcessJob.terminate child
        pure (none : Option String)
      catch error => pure (some error.toString)
      let observed : Option String × Option UInt32 ← if System.Platform.isWindows then
        try pure (none, ← child.tryWait)
        catch error => pure (some error.toString, none)
      else pure (none, none) -- POSIX tryWait would reap before the one final wait.
      let waited : Option String × Option UInt32 ← try
        pure (none, some (← child.wait))
      catch error => pure (some error.toString, none)
      IO.println <| (Json.mkObj [
        ("windows", toJson System.Platform.isWindows), ("job", toJson useJob),
        ("iteration", toJson iteration), ("pid", toJson pid.toNat),
        ("scope_error", toJson scopeError?), ("terminate_error", toJson terminateError?),
        ("try_wait_error", toJson observed.1), ("try_wait_result", toJson (observed.2.map UInt32.toNat)),
        ("wait_error", toJson waited.1), ("wait_result", toJson (waited.2.map UInt32.toNat))
      ]).compress

private def checkRealHelperDiagnostic (helper root : String) : IO Unit := do
  for iteration in [:8] do
    let result ← runLakeHelperServerEnv helper { root }
    let response := match result with
      | .ok value => Response.success (toJson value)
      | .error failure => failure.toResponse
    IO.println <| (Json.mkObj [
      ("iteration", toJson iteration), ("response", toJson response)
    ]).compress
    (← IO.getStdout).flush

private def runHelper (args : List String) : IO Unit := do
  let helper :: operation :: options := args
    | throw <| IO.userError "expected HELPER OPERATION [TIMEOUT-MS [CANCEL-FILE]]"
  let some operation := LakeHelperOperation.ofString? operation
    | throw <| IO.userError "invalid fixture operation"
  let request ← IO.ofExcept <| Json.parse (← (← IO.getStdin).readToEnd)
  let timeoutMs ← match options.head? with
    | none => pure 30000
    | some value => match value.toNat? with
      | some timeout => pure timeout
      | none => throw <| IO.userError "invalid fixture timeout"
  let cancelled ← IO.mkRef false
  let done ← IO.mkRef false
  let watcher? ← (options[1]?).mapM fun path =>
    IO.asTask (prio := Task.Priority.dedicated) do
      repeat
        if ← done.get then break
        if ← (System.FilePath.mk path).pathExists then
          cancelled.set true
          break
        IO.sleep 10
  let result : Except BrokerFailure Json ← try
    runLakeHelperRequest helper operation request { cancelRef? := some cancelled, timeoutMs }
  finally
    done.set true
    if let some watcher := watcher? then discard <| IO.wait watcher
  let response := match result with
    | .ok value => Response.success value
    | .error failure => failure.toResponse
  IO.println (toJson response).compress

/-- Non-shipped client for exercising owned helper messages and retained-child cleanup. -/
def main (args : List String) : IO Unit := do
  match args with
  | ["--process-job-exit"] => pure ()
  | ["--process-job-exited-groups"] => checkExitedGroups
  | ["--process-job-hold-diagnostic"] => holdForJobDiagnostic
  | ["--process-job-close-diagnostic"] => checkJobCloseDiagnostic
  | ["--real-helper-diagnostic", helper, root] => checkRealHelperDiagnostic helper root
  | _ => runHelper args
