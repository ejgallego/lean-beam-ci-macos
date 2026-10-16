/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

import Lean
import Lean.Elab.Term

namespace Beam

elab "beamCompiledLeanRevision" : term =>
  Lean.Elab.Term.elabTerm (Lean.Syntax.mkStrLit Lean.githash) none

/-- Frozen during elaboration, independently of the Lean runtime loaded by the executable. -/
def compiledCompilerRevision : String := beamCompiledLeanRevision

/-- Revision agreement is a necessary compatibility check, not a complete build-configuration ABI proof. -/
def compilerRevisionsMatch (compiled loaded selected : String) : Bool :=
  !compiled.isEmpty && compiled == loaded && compiled == selected

end Beam
