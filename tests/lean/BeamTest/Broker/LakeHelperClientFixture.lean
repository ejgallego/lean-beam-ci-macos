/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Beam.Broker.LakeHelper
import BeamTest.Broker.HelperOutcomeTest
import BeamTest.Broker.SaveTraceFixture

open Lean Beam.Broker

private def awaitExited (scope : Beam.ProcessJob.Scope) : IO Unit := do
  let deadline := (← IO.monoMsNow) + 5000
  repeat
    match ← scope.poll with
    | .exited => return
    | .running =>
        if (← IO.monoMsNow) >= deadline then
          throw <| IO.userError "fixture child did not exit"
        IO.sleep 10

/-- Keep an exited session leader unreaped: its remaining group may contain only zombies. -/
private def checkExitedGroups : IO Unit := do
  for _ in [:16] do
    let child ← Beam.OwnedProcess.spawn (← IO.appPath).toString #["--process-job-exit"]
    let completed ← Beam.ProcessJob.withChild child fun scope => do
      child.stdin.writeAll "exit\n".toUTF8
      awaitExited scope
      for _ in [:2] do
        unless (← scope.poll) == .exited do
          throw <| IO.userError "exit observation consumed the retained child identity"
      return scope
    discard <| IO.ofExcept completed.cleanup
    unless (← child.finish) == 0 && (← child.exitCode?) == some 0 do
      throw <| IO.userError "collection changed the leader's exit status"
    if let .ok _ ← (child.stdin.writeAll "after exit".toUTF8).toBaseIO then
      throw <| IO.userError "write unexpectedly succeeded after the child's pipe closed"
    let scope ← IO.ofExcept completed.operation
    -- An escaped observation cannot touch the old PID after scope teardown.
    let rejected ← try scope.poll *> pure false catch _ => pure true
    unless rejected do throw <| IO.userError "closed process scope still allowed observation"
  let child ← Beam.OwnedProcess.spawn (← IO.appPath).toString #["--process-job-exit"]
  -- This child waits for permission forever unless exceptional teardown kills and waits for it.
  let completed ← Beam.ProcessJob.withChild child fun _ =>
    (throw <| IO.userError "fixture action failed" : IO Unit)
  discard <| IO.ofExcept completed.cleanup
  match completed.operation with
  | .error (.userError "fixture action failed") => pure ()
  | _ => throw <| IO.userError "process scope lost its action failure"
  IO.println "process scopes: retained exit and closed observation passed"

private def pipeEcho (args : List String) : IO Unit := do
  unless args == ["", "two words", "α", "quote\"and\\", "tail\\\\"] do
    throw <| IO.userError "owned launcher changed argv"
  let input ← IO.getStdin
  let output ← IO.getStdout
  repeat
    let chunk ← input.read 4096
    if chunk.isEmpty then break
    output.write chunk
  output.flush

private def checkPipes : IO Unit := do
  let payload := ByteArray.mk <| (List.range 131073).toArray.map Nat.toUInt8
  let child ← Beam.OwnedProcess.spawn (← IO.appPath).toString
    #["--pipe-echo", "", "two words", "α", "quote\"and\\", "tail\\\\"]
  try
    let reader ← IO.asTask (prio := .dedicated) do
      let mut bytes := ByteArray.empty
      repeat
        let chunk ← child.stdout.readSome 3001
        if chunk.isEmpty then break
        bytes := bytes ++ chunk
      return bytes
    child.stdin.writeAll payload
    child.stdin.close
    child.stdin.close
    unless (← IO.ofExcept (← IO.wait reader)) == payload do
      throw <| IO.userError "large pipe transfer or EOF failed"
    unless (← child.stderr.readSome 1).isEmpty do
      throw <| IO.userError "expected EOF on stderr"
  finally discard child.finish
  -- Closing while a read is acquiring its reference can also win the race. Repeat
  -- that case until a blocked read survives close and delivers the child's one byte.
  for _ in [:8] do
    let child ← Beam.OwnedProcess.spawn (← IO.appPath).toString #["--pipe-gate"]
    try
      let entered ← IO.Promise.new
      let read ← IO.asTask (prio := .dedicated) do
        entered.resolve ()
        child.stdout.readSome 1
      discard <| IO.wait entered.result?
      IO.sleep 20
      child.stdout.close
      child.stdout.close
      let rejected ← (child.stdout.readSome 1).toBaseIO
      if let .ok _ := rejected then throw <| IO.userError "closed endpoint still accepts reads"
      child.stdin.writeAll "x\n".toUTF8
      if let .ok bytes ← IO.wait read then
        unless bytes == "x".toUTF8 do throw <| IO.userError "read lost its borrowed endpoint"
        return
    finally discard child.finish
  throw <| IO.userError "failed to exercise close during an outstanding pipe read"

private def runHelper (args : List String) : IO Unit := do
  let helper :: operation :: options := args
    | throw <| IO.userError "expected HELPER OPERATION [CANCEL-FILE]"
  let some operation := LakeHelperOperation.ofString? operation
    | throw <| IO.userError "invalid fixture operation"
  let request ← IO.ofExcept <| Json.parse (← (← IO.getStdin).readToEnd)
  let cancelled ← IO.mkRef false
  let output ← Beam.WorkspaceOutput.Buffer.create
  let some publisher ← output.borrow
    | throw <| IO.userError "new fixture output buffer was closed"
  let done ← IO.mkRef false
  let watcher? ← options.head?.mapM fun path =>
    IO.asTask (prio := Task.Priority.dedicated) do
      repeat
        if ← done.get then break
        if ← (System.FilePath.mk path).pathExists then
          cancelled.set true
          break
        IO.sleep 10
  let result : Except BrokerFailure Json ← try
    LakeHelperJob.runRequest helper operation request {
      cancelRef? := some cancelled, output? := some publisher }
  finally
    done.set true
    if let some watcher := watcher? then discard <| IO.wait watcher
  output.seal
  -- This fixture retains the bounded tail until completion; live pipe delivery is
  -- checked by the gated producer in the existing CLI daemon test executable.
  let stderr ← IO.getStderr
  while true do
    match ← output.poll with
    | .chunk chunk => stderr.putStr chunk.text
    | .closed => break
    | .waiting _ => throw <| IO.userError "sealed fixture output was still waiting"
  stderr.flush
  let response := match result with
    | .ok value => Response.success value
    | .error failure => failure.toResponse
  IO.println (toJson response).compress

/-- Exercise a real post-response temporary-directory cleanup failure without a production hook. -/
private def cleanupFaultHelper (directory : System.FilePath) : IO Unit := do
  let stdin ← IO.getStdin
  unless (← stdin.getLine).trimAscii == "go" do
    throw <| IO.userError "fixture helper expected assignment before project code"
  let owner ← IO.asTask (prio := Task.Priority.dedicated) do
    try discard <| stdin.readToEnd finally Beam.ProcessJob.parentLost
  let request ← LakeHelperJob.readJson (LakeHelperJob.requestPath directory)
  let response ← IO.ofExcept <| LakeHelperResponse.decode (α := Json) (← IO.ofExcept <| request.getObjVal? "response")
  let marker ← IO.ofExcept <| request.getObjValAs? System.FilePath "marker"
  let receipt ← IO.ofExcept <| request.getObjValAs? System.FilePath "receipt"
  let obstacle := directory / "cleanup-denied"
  IO.FS.createDir obstacle
  IO.FS.writeFile (obstacle / "owned") "fixture-owned cleanup obstacle\n"
  IO.FS.writeFile receipt (Json.mkObj [
    ("directory", toJson obstacle), ("pid", toJson (← IO.Process.getPID).toNat)]).compress
  IO.setAccessRights obstacle {}
  IO.FS.writeFile marker "operation finished\n"
  LakeHelperJob.publish directory response
  -- The real client owns termination and reaping, including owner-loss handling.
  discard <| IO.wait owner

/-- Non-shipped client for exercising owned helper messages and retained-child cleanup. -/
def main (args : List String) : IO Unit := do
  match args with
  | ["save-trace-fixture", root] =>
      BeamTest.Broker.SaveTraceFixture.write (System.FilePath.mk root)
  | ["write-prepared-trace", root] =>
      BeamTest.Broker.SaveTraceFixture.writePrepared (System.FilePath.mk root)
  | ["lake-helper", "server-env", directory] => cleanupFaultHelper directory
  | "--pipe-echo" :: rest => pipeEcho rest
  | ["--pipe-gate"] => do
      discard <| (← IO.getStdin).getLine
      (← IO.getStdout).putStr "x"
      (← IO.getStdout).flush
  | ["--owned-pipes"] => checkPipes
  | ["--helper-outcomes"] => BeamTest.Broker.HelperOutcomeTest.run
  | ["--process-job-exit"] => do
      unless (← (← IO.getStdin).getLine).trimAscii == "exit" do
        throw <| IO.userError "fixture expected exit permission after job assignment"
  | ["--process-job-exited-groups"] => checkExitedGroups
  | _ => runHelper args
