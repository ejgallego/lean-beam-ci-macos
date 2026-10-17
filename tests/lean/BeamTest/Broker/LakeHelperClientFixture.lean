/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Beam.Broker.LakeHelper

open Lean Beam.Broker

/-- Non-shipped client for exercising the actual owned helper protocol, including invalid data. -/
def main (args : List String) : IO Unit := do
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
