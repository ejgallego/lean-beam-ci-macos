/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Beam.LSP.ImportBasis
import BeamTest.LSP.Requests.Interference
import BeamTest.LSP.Requests.Support

open Lean
open BeamTest.LSP.Scenario
open BeamTest.LSP.Requests.Interference
open BeamTest.LSP.Requests.Support

namespace BeamTest.LSP.Requests.ImportBasis.BasicTest

private def depAFixture : System.FilePath :=
  "tests/lean/BeamTest/Fixtures/Deps/DepA.lean"

private def requireStatus
    (label : String) (expected : Beam.LSP.ImportBasis.Status)
    (result : Beam.LSP.ImportBasis.CheckResult) : ScenarioM Unit := do
  unless result.status == expected && result.changedImports.isEmpty do
    throw <| IO.userError s!"{label}: expected status {(toJson expected).compress}, got {(toJson result).compress}"

/-- A basis captured when the header loads stays current while the import traces are unchanged. -/
def checkImportBasisCapturedAtLoad : ScenarioM Unit := do
  let doc ← openDoc depAFixture
  let captureReq ← sendImportBasisCapture doc
  discard <| awaitReq captureReq
  let checkReq ← sendImportBasisCheck doc
  requireStatus "importBasis check" .current (← awaitResponseAs checkReq)
  -- The broker captures again after each edit; the basis stays current with unchanged traces.
  syncWhitespacePrefixEdit doc
  let recaptureReq ← sendImportBasisCapture doc
  discard <| awaitReq recaptureReq
  let editedReq ← sendImportBasisCheck doc
  requireStatus "importBasis check after edit" .current (← awaitResponseAs editedReq)
  closeDoc doc

/-- A check never captures: a header loaded without an early capture has an unknown basis. -/
def checkImportBasisWithoutCaptureIsUnknown : ScenarioM Unit := do
  let doc ← openDoc depAFixture
  let barrierReq ← sendDiagnosticsBarrier doc
  discard <| awaitReq barrierReq
  let checkReq ← sendImportBasisCheck doc
  requireStatus "importBasis late check" .unknown (← awaitResponseAs checkReq)
  closeDoc doc

/-- A module Beam saved at or after the load point counts as changed, whatever its trace says. -/
def checkImportBasisSavedSinceLoad : ScenarioM Unit := do
  let doc ← openDoc depAFixture
  let captureReq ← sendImportBasisCapture doc (eventSeq := 5)
  discard <| awaitReq captureReq
  let earlierReq ← sendImportBasisCheck doc
    (publishedModules := #[{ module := "BeamTest.Fixtures.Deps.DepB", eventSeq := 4 }])
  requireStatus "importBasis save before load" .current (← awaitResponseAs earlierReq)
  let laterReq ← sendImportBasisCheck doc
    (publishedModules := #[{ module := "BeamTest.Fixtures.Deps.DepB", eventSeq := 5 }])
  let later : Beam.LSP.ImportBasis.CheckResult ← awaitResponseAs laterReq
  unless later.status == .changed && later.changedImports == #["BeamTest.Fixtures.Deps.DepB"] do
    throw <| IO.userError s!"importBasis save at load: expected DepB changed, got {(toJson later).compress}"
  closeDoc doc

/--
A body edit reuses the header environment, and the broker captures it again with a later sequence.
The load boundary stays at the first capture, even while the first is still pending.
-/
def checkImportBasisLaterCaptureKeepsLoadBoundary : ScenarioM Unit := do
  let doc ← openDoc depAFixture
  let firstReq ← sendImportBasisCapture doc (eventSeq := 5)
  changeDoc doc { line := 8, character := 0, delete := "", insert := "\n" }
  let laterReq ← sendImportBasisCapture doc (eventSeq := 9)
  discard <| awaitReq laterReq
  discard <| awaitReq firstReq
  let checkReq ← sendImportBasisCheck doc
    (publishedModules := #[{ module := "BeamTest.Fixtures.Deps.DepB", eventSeq := 7 }])
  let check : Beam.LSP.ImportBasis.CheckResult ← awaitResponseAs checkReq
  unless check.status == .changed && check.changedImports == #["BeamTest.Fixtures.Deps.DepB"] do
    throw <| IO.userError s!"importBasis load boundary: expected DepB changed, got {(toJson check).compress}"
  closeDoc doc

private def depBTrace : System.FilePath :=
  ".lake/build/lib/lean/BeamTest/Fixtures/Deps/DepB.trace"

/--
A capture that could not resolve a trace leaves its environment unknown. Once the trace is back,
a capture after a body edit reuses that environment and must not make it current.
-/
def checkImportBasisFailedCaptureStaysUnknown : ScenarioM Unit := do
  let doc ← openDoc depAFixture
  -- Loading the header runs `lake setup-file`, which would rebuild a missing trace.
  discard <| awaitReq (← sendDiagnosticsBarrier doc)
  let aside := depBTrace.withExtension "trace-aside"
  IO.FS.rename depBTrace aside
  try
    let firstReq ← sendImportBasisCapture doc (eventSeq := 5)
    discard <| awaitReq firstReq
  finally
    IO.FS.rename aside depBTrace
  changeDoc doc { line := 8, character := 0, delete := "", insert := "\n" }
  let laterReq ← sendImportBasisCapture doc (eventSeq := 9)
  discard <| awaitReq laterReq
  let checkReq ← sendImportBasisCheck doc
  let check : Beam.LSP.ImportBasis.CheckResult ← awaitResponseAs checkReq
  unless check.status == .unknown && check.unresolvedImports == #["BeamTest.Fixtures.Deps.DepB"] do
    throw <| IO.userError s!"importBasis failed capture: expected DepB unresolved, got {(toJson check).compress}"
  closeDoc doc

def checkImportBasisStaleVersion : ScenarioM Unit := do
  let doc ← openDoc depAFixture
  let captureReq ← sendImportBasisCapture doc
  discard <| awaitReq captureReq
  let staleReq ← sendImportBasisCheck doc (version? := some 7)
  expectErrorContains staleReq (Json.mkObj [("code", toJson "contentModified")])
  closeDoc doc

def run : ScenarioM Unit := do
  checkImportBasisCapturedAtLoad
  checkImportBasisWithoutCaptureIsUnknown
  checkImportBasisSavedSinceLoad
  checkImportBasisLaterCaptureKeepsLoadBoundary
  checkImportBasisFailedCaptureStaysUnknown
  checkImportBasisStaleVersion

end BeamTest.LSP.Requests.ImportBasis.BasicTest
