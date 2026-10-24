/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Author: Emilio J. Gallego Arias
-/

namespace Beam.Cli

/-- The wrapper session operation selected for control admission or recovery guidance. -/
inductive WrapperSessionCommand where
  | serve
  | status
  | stop
  | recoverGeneration (generation : String)

end Beam.Cli
