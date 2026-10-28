-- Copyright (c) 2026 Lean FRO LLC. All rights reserved.
-- Released under Apache 2.0 license as described in the file LICENSE.
import Lean

def main : IO Unit := do
  let version := Lean.libUVVersion
  if version < 0x013500 then
    throw <| IO.userError "The experimental Windows SDK must load libuv >= 1.53.0"
  IO.println <| (Lean.Json.mkObj [
    ("packed", Lean.toJson version),
    ("version", Lean.toJson s!"{version >>> 16}.{(version >>> 8) &&& 255}.{version &&& 255}")
  ]).compress
