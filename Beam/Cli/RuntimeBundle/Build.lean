/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Lean
import Beam.Cli.RuntimeBundle.Artifacts
import Beam.Cli.RuntimeBundle.Fingerprint
import Beam.Cli.RuntimeBundle.Source
import Beam.Path

open Lean

namespace Beam.Cli

private def copyFileInto (srcRoot dstRoot srcPath : System.FilePath) : IO Unit := do
  let rel := Beam.pathRelativeToRootOrSelf srcRoot srcPath
  let dst := dstRoot / rel
  if let some parent := dst.parent then
    IO.FS.createDirAll parent
  IO.FS.writeBinFile dst (← IO.FS.readBinFile srcPath)

private def copyTreeInto (srcRoot dstRoot : System.FilePath) : IO Unit := do
  let files ← collectTreeFiles srcRoot
  for path in files do
    copyFileInto srcRoot dstRoot path

/-- Copy the Beam sources into a fresh workspace; construction never reuses a previous build. -/
def syncBundleWorkspace (home workspace : System.FilePath) : IO Unit := do
  if ← workspace.pathExists then
    throw <| IO.userError s!"construction workspace already exists: {workspace}"
  IO.FS.createDirAll workspace
  let files ← collectBundleSourceFiles home
  for path in files do
    copyFileInto home workspace path
  let packagesDir := home / ".lake" / "packages"
  if ← packagesDir.pathExists then
    copyTreeInto packagesDir (workspace / ".lake" / "packages")

private def samePath (left right : System.FilePath) : IO Bool := do
  try
    let left ← IO.FS.realPath left
    let right ← IO.FS.realPath right
    pure (left.normalize == right.normalize)
  catch _ =>
    pure (left.normalize == right.normalize)

private def symlinkForce (src dst : System.FilePath) : IO Unit := do
  try
    IO.FS.removeFile dst
  catch _ =>
    pure ()
  let out ← IO.Process.output {
    cmd := "ln"
    args := #["-s", src.toString, dst.toString]
  }
  if out.exitCode != 0 then
    throw <| IO.userError s!"failed to create symlink {dst} -> {src}\n{out.stderr}"

private def copyExecutable (src dst : System.FilePath) : IO Unit := do
  IO.FS.writeBinFile dst (← IO.FS.readBinFile src)
  let x : IO.AccessRight := {read := true, write := true, execution := true}
  let rx : IO.AccessRight := {read := true, write := false, execution := true}
  IO.setAccessRights dst ⟨x, rx, rx⟩

private def prepareNonstandardLakeHome
    (buildDir leanPrefix libDir : System.FilePath) : IO System.FilePath := do
  if System.Platform.isWindows then
    throw <| IO.userError
      "nonstandard Lean libdir toolchains are not supported by the standalone runtime construction on Windows"
  let lakeHome := buildDir / "lake-home"
  let buildDir := lakeHome / ".lake" / "build"
  let binDir := buildDir / "bin"
  let sharedLibDir := buildDir / "lib"
  let lakeLibDir := sharedLibDir / "lean"
  IO.FS.createDirAll binDir
  IO.FS.createDirAll sharedLibDir
  IO.FS.createDirAll lakeLibDir
  copyExecutable (leanPrefix / "bin" / "lake") (binDir / "lake")
  for entry in ← libDir.readDir do
    let name := entry.fileName
    if name.startsWith "lib" then
      pure ()
    else
      symlinkForce entry.path (lakeLibDir / name)
  let standardLibDir := leanPrefix / "lib" / "lean"
  for entry in ← standardLibDir.readDir do
    let name := entry.fileName
    if name.startsWith "lib" then
      symlinkForce entry.path (sharedLibDir / name)
      symlinkForce entry.path (lakeLibDir / name)
  pure lakeHome

private structure LakeInvocation where
  cmd : String
  args : Array String
  env : Array (String × Option String) := #[]

private def lakeInvocationFor (buildDir : System.FilePath)
    (fingerprint : ToolchainFingerprint) :
    IO LakeInvocation := do
  let leanPrefix := System.FilePath.mk fingerprint.leanPrefix
  let exe := if System.Platform.isWindows then ".exe" else ""
  let cleanEnv : Array (String × Option String) := #[
    ("LEAN_PATH", none), ("LEAN_SRC_PATH", none),
    ("LEAN_CC", none), ("LEAN_AR", none), ("LEAN_ABORT_ON_PANIC", none)
  ]
  let default : LakeInvocation := {
    cmd := (leanPrefix / "bin" / ("lake" ++ exe)).toString
    args := #[]
    env := #[("LEAN", some (leanPrefix / "bin" / ("lean" ++ exe)).toString),
      ("LEAN_SYSROOT", some leanPrefix.toString), ("LAKE_HOME", none)] ++ cleanEnv
  }
  let libDir := System.FilePath.mk fingerprint.leanLibDir
  let standardLibDir := leanPrefix / "lib" / "lean"
  if ← samePath libDir standardLibDir then
    pure default
  else
    let lakeHome ← prepareNonstandardLakeHome buildDir leanPrefix libDir
    pure {
      cmd := (lakeHome / ".lake" / "build" / "bin" / "lake").toString
      args := #[]
      env := #[
        ("LAKE_HOME", some lakeHome.toString),
        ("LEAN", some (leanPrefix / "bin" / "lean").toString),
        ("LEAN_SYSROOT", none)
      ] ++ cleanEnv
    }

/-- Query construction inputs from the selected Lake SDK, without scanning its library directory. -/
def buildSharedRuntimePaths (buildDir workspace : System.FilePath)
    (fingerprint : ToolchainFingerprint) : IO (Array String) := do
  let lake ← lakeInvocationFor buildDir fingerprint
  let output ← IO.Process.output {
    cmd := lake.cmd
    args := lake.args ++ #["script", "run", "beamSharedRuntimeFiles"]
    env := lake.env
    cwd := workspace.toString
  }
  unless output.exitCode == 0 do
    throw <| IO.userError s!"selected Lake could not report its shared-runtime inputs: {output.stderr}"
  let paths ← IO.ofExcept <| (Json.parse output.stdout >>= fromJson? (α := Array String)).mapError IO.userError
  unless validSharedRuntimePaths paths do
    throw <| IO.userError "selected Lake reported invalid shared-runtime inputs"
  pure paths

private def constructionBuildFailureMessage (toolchain : String) (buildDir : System.FilePath)
    (stdout stderr : String) : String :=
  String.intercalate "\n" [
    s!"failed to construct Beam runtime for toolchain {toolchain}",
    s!"construction workspace: {bundleWorkspaceFor buildDir}",
    "installation runs `lake build` and may need network access on a cold machine",
    "normal Beam execution never constructs missing targets",
    "",
    "lake stdout:",
    if stdout.trimAscii.isEmpty then "(empty)" else stdout,
    "",
    "lake stderr:",
    if stderr.trimAscii.isEmpty then "(empty)" else stderr
  ]

private def bundleQualificationFailureMessage
    (toolchain : String)
    (workspace probe : System.FilePath)
    (stdout stderr : String) : String :=
  String.intercalate "\n" [
    s!"failed to qualify beam bundle for toolchain {toolchain}",
    "the bundle compiled, but its freshly built Lean plugin did not pass the local load/elaboration probe",
    s!"bundle workspace: {workspace}",
    s!"qualification source: {probe}",
    "",
    "qualification stdout:",
    if stdout.trimAscii.isEmpty then "(empty)" else stdout,
    "",
    "qualification stderr:",
    if stderr.trimAscii.isEmpty then "(empty)" else stderr
  ]

private def qualifyToolchainBundle
    (buildDir workspace : System.FilePath)
    (toolchain : String)
    (lake : LakeInvocation) : IO Unit := do
  let qualificationDir := buildDir / "qualification"
  let probe := qualificationDir / "Probe.lean"
  IO.FS.createDirAll qualificationDir
  IO.FS.writeFile probe "example : True := by\n  trivial\n"
  let plugin ← Beam.resolveExistingPath (leanBundlePathsFor workspace).plugin
  let probe ← Beam.resolveExistingPath probe
  let out ← IO.Process.output {
    cmd := lake.cmd
    args := lake.args ++ #[
      "env",
      "lean",
      s!"--plugin={plugin}",
      "-Dexperimental.module=true",
      probe.toString
    ]
    env := lake.env
    cwd := workspace.toString
  }
  if out.exitCode != 0 then
    throw <| IO.userError <|
      bundleQualificationFailureMessage toolchain workspace probe out.stdout out.stderr
  IO.FS.removeDirAll qualificationDir

/-- Build and qualify one target in a fresh directory owned by the caller. -/
def buildVerifiedTarget (hashes : ConstructionHash.Context) (home : System.FilePath) (toolchain : String)
    (fingerprint : ToolchainFingerprint) (buildDir : System.FilePath) : IO VerifiedBundle := do
  let workspace := bundleWorkspaceFor buildDir
  syncBundleWorkspace home workspace
  IO.eprintln s!"building beam runtime for {toolchain}"
  let lake ← lakeInvocationFor buildDir fingerprint
  let out ← IO.Process.output {
    cmd := lake.cmd
    args := lake.args ++ #["build", "Beam.LSP:shared", "beam-daemon"]
    env := lake.env
    cwd := workspace.toString
  }
  if out.exitCode != 0 then
    throw <| IO.userError <| constructionBuildFailureMessage toolchain buildDir out.stdout out.stderr
  let sharedLibraries ← buildSharedRuntimePaths buildDir workspace fingerprint
  let bundle ← VerifiedBundle.capture hashes workspace sharedLibraries
  qualifyToolchainBundle buildDir workspace toolchain lake
  bundle.requireUnchanged
  pure bundle

end Beam.Cli
