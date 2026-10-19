/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Lean
import Beam.LSP.Lib.NativeLib
import Beam.System

open Lean

namespace Beam.Cli

structure LeanBundlePaths where
  daemon : System.FilePath
  plugin : System.FilePath
  sharedLibraries : Array System.FilePath := #[]
  deriving Repr

def validatedLeanToolchainsPath (home : System.FilePath) : System.FilePath :=
  home / "validated-lean-toolchains"

def compatibleLeanReleaseLinesPath (home : System.FilePath) : System.FilePath :=
  home / "compatible-lean-release-lines"

def customLeanToolchainsPath (home : System.FilePath) : System.FilePath :=
  home / "custom-lean-toolchains"

/-- The Lake workspace inside one target's temporary construction directory. -/
def bundleWorkspaceFor (buildDir : System.FilePath) : System.FilePath :=
  buildDir / "workspace"

def leanBundlePathsFor (workspace : System.FilePath) : LeanBundlePaths :=
  {
    daemon := workspace / ".lake" / "build" / "bin" /
      (if System.Platform.isWindows then "beam-daemon.exe" else "beam-daemon")
    plugin := Beam.LSP.Lib.pluginSharedLibPath (workspace / ".lake" / "build" / "lib")
  }

end Beam.Cli
