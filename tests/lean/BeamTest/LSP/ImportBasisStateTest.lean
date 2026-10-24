/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Beam.LSP.ImportBasis

open Lean
open Beam.LSP.ImportBasis

/-!
Worker-local import basis transitions that a live worker cannot be made to hit on demand: a
capture whose collection throws must still decide its environment's basis.
-/

namespace BeamTest.LSP.ImportBasisStateTest

private def requireStatus (label : String) (expected : Status) (result : Result) : IO Unit := do
  unless result.status == expected do
    throw <| IO.userError s!"{label}: expected {(toJson expected).compress}, got {(toJson result).compress}"

/-- A failed capture stays unknown; a later capture of the same environment cannot make it current. -/
def checkFailedCaptureKeepsEnvironmentUnknown : IO Unit := do
  let env ← mkEmptyEnvironment
  Internal.captureFailing env 5
  requireStatus "failed capture" .unknown (← Internal.check env #[])
  -- A body edit reuses the environment and captures it again with a later sequence.
  Internal.capture env 9
  requireStatus "recapture of a failed environment" .unknown (← Internal.check env #[])
  -- A new header environment is captured afresh.
  let reloaded ← mkEmptyEnvironment
  Internal.capture reloaded 11
  requireStatus "capture of a new environment" .current (← Internal.check reloaded #[])
  requireStatus "check of the replaced environment" .unknown (← Internal.check env #[])

end BeamTest.LSP.ImportBasisStateTest

def main : IO Unit :=
  BeamTest.LSP.ImportBasisStateTest.checkFailedCaptureKeepsEnvironmentUnknown
