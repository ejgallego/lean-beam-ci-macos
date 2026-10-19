/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Lean
import Beam.Cli.RuntimeBundle.Paths
import Beam.Path
import Beam.System
import Beam.Cli.ConstructionHash

open Lean

namespace Beam.Cli

/-- The build reports SDK dependencies explicitly; construction never infers them from files. -/
def validSharedRuntimePaths (paths : Array String) : Bool := Id.run do
  if paths.isEmpty then return false
  let directory := if System.Platform.isWindows then "bin" else "lib/lean"
  let mut seen : Std.HashSet String := {}
  for path in paths do
    if !Beam.Installation.relativePath path ||
        (System.FilePath.mk path).parent != some (System.FilePath.mk directory) || seen.contains path then
      return false
    seen := seen.insert path
  return true

private def outputPaths (workspace : System.FilePath) (sharedLibraries : Array String) :
    Array System.FilePath :=
  let paths := leanBundlePathsFor workspace
  #[paths.daemon, paths.plugin] ++ sharedLibraries.map (fun path => workspace / ".lake/build" / System.FilePath.mk path)

private def relativeOutput (workspace path : System.FilePath) : String :=
  String.intercalate "/" (System.FilePath.mk (Beam.pathRelativeToRootOrSelf workspace path)).components

/-- Hash the complete declared output set of one target build. -/
def snapshotBundleArtifacts (hashes : ConstructionHash.Context) (workspace : System.FilePath) (sharedLibraries : Array String) :
    IO (Array Beam.Installation.Artifact) := do
  (outputPaths workspace sharedLibraries).mapM fun path => do
    if (← Beam.pathIsLink path) || !(← Beam.regularNonSymlinkFile path) then
      throw <| IO.userError s!"missing regular construction output: {path}"
    let relative := relativeOutput workspace path
    pure { source := relative, destination := relative, hash := ← hashes.fileHash path }

/-- One target build's complete byte inventory, captured before qualification. -/
structure VerifiedBundle where
  private mk ::
  private hashes : ConstructionHash.Context
  private workspace : System.FilePath
  private sharedLibraries : Array String
  private artifacts : Array Beam.Installation.Artifact

/-- Record the declared outputs of a completed build. -/
def VerifiedBundle.capture (hashes : ConstructionHash.Context) (workspace : System.FilePath)
    (sharedLibraries : Array String) : IO VerifiedBundle := do
  unless validSharedRuntimePaths sharedLibraries do
    throw <| IO.userError "construction must name complete relative shared-library inputs"
  pure ⟨hashes, workspace, sharedLibraries, ← snapshotBundleArtifacts hashes workspace sharedLibraries⟩

/-- Qualification must leave every recorded output byte-identical. -/
def VerifiedBundle.requireUnchanged (bundle : VerifiedBundle) : IO Unit := do
  unless (← snapshotBundleArtifacts bundle.hashes bundle.workspace bundle.sharedLibraries) == bundle.artifacts do
    throw <| IO.userError "construction outputs changed after they were recorded"

def VerifiedBundle.paths (bundle : VerifiedBundle) : LeanBundlePaths :=
  { leanBundlePathsFor bundle.workspace with
    sharedLibraries := bundle.sharedLibraries.map (fun path => bundle.workspace / ".lake/build" / System.FilePath.mk path) }

/-- Copy only recorded outputs, then verify the staged bytes against the qualified record. -/
def VerifiedBundle.copyFile (bundle : VerifiedBundle) (source destination : System.FilePath) : IO Unit := do
  let relative := relativeOutput bundle.workspace source
  let some artifact := bundle.artifacts.find? (·.source == relative)
    | throw <| IO.userError s!"unrecorded construction output: {source}"
  if (← Beam.pathIsLink source) || !(← Beam.regularNonSymlinkFile source) then
    throw <| IO.userError s!"missing regular construction output: {source}"
  IO.FS.createDirAll (destination.parent.getD ".")
  IO.FS.writeBinFile destination (← IO.FS.readBinFile source)
  unless (← bundle.hashes.fileHash destination) == artifact.hash do
    throw <| IO.userError s!"construction output changed before staging: {source}"

end Beam.Cli
