/-
Copyright (c) 2026 Lean FRO LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
-/
import Std.Sync.Mutex

namespace Beam.OwnedProcess

private opaque Resource : NonemptyType
private abbrev Raw := Resource.type
private instance : Nonempty Raw := Resource.property

private structure Launch where
  process : Raw
  input : Raw
  output : Raw
  error : Raw
  pid : UInt32
  deriving Nonempty

@[extern "lean_beam_owned_spawn"] private opaque spawnRaw
    (cmd : @& String) (args : @& Array String) (cwd : @& Option String) : IO Launch
@[extern "lean_beam_owned_read"] private opaque readRaw
    (endpoint : @& Raw) (count : USize) : IO ByteArray
@[extern "lean_beam_owned_write"] private opaque writeRaw
    (endpoint : @& Raw) (bytes : @& ByteArray) : IO Unit
@[extern "lean_beam_owned_peek"] private opaque peekRaw (process : @& Raw) : IO (Option UInt32)
@[extern "lean_beam_owned_signal"] private opaque signalRaw (process : @& Raw) : IO Unit
@[extern "lean_beam_owned_collect"] private opaque collectRaw (process : @& Raw) : IO UInt32

private abbrev Endpoint := Std.Mutex (Option Raw)

private def borrowEndpoint (endpoint : Endpoint) : IO Raw := endpoint.atomically do
  let some handle ← get | throw <| IO.userError "Beam process pipe is closed"
  return handle

structure Reader where
  private handle : Endpoint
structure Writer where
  private handle : Endpoint

/-- A read retains its descriptor until it returns, including across a concurrent close. -/
def Reader.readSome (r : Reader) (count : USize) : IO ByteArray := do
  readRaw (← borrowEndpoint r.handle) count

def Writer.writeAll (w : Writer) (bytes : ByteArray) : IO Unit := do
  writeRaw (← borrowEndpoint w.handle) bytes

def Reader.close (r : Reader) : BaseIO Unit := r.handle.atomically (set (none : Option Raw))
def Writer.close (w : Writer) : BaseIO Unit := w.handle.atomically (set (none : Option Raw))

structure Child where
  private process : Std.Mutex Raw
  stdin : Writer
  stdout : Reader
  stderr : Reader
  pid : UInt32

private initialize spawnLock : Std.Mutex Unit ← Std.Mutex.new ()

/-- Windows requires the three child pipe handles to be inheritable during CreateProcessW.
Serialize that interval with ordinary backend launches on SDKs that inherit all marked handles. -/
def withWindowsSpawnLock (launch : IO α) : IO α :=
  if System.Platform.isWindows then spawnLock.atomically launch else launch

/-- Launch a resolved executable in a new POSIX session or a Windows kill-on-close job. -/
def spawn (cmd : String) (args : Array String) (cwd : Option System.FilePath := none) : IO Child := do
  let path : System.FilePath := cmd
  let path ← if path.isAbsolute then pure path else pure ((← IO.currentDir) / path)
  let raw ← withWindowsSpawnLock <| spawnRaw path.toString args (cwd.map (·.toString))
  return ⟨← Std.Mutex.new raw.process, ⟨← Std.Mutex.new (some raw.input)⟩,
    ⟨← Std.Mutex.new (some raw.output)⟩, ⟨← Std.Mutex.new (some raw.error)⟩, raw.pid⟩

/-- Non-consuming observation. The original exit code remains available after collection. -/
def Child.exitCode? (child : Child) : IO (Option UInt32) := child.process.atomically do
  peekRaw (← get)

/-- Terminate remaining members while the leader identity is retained. -/
def Child.terminate (child : Child) : IO Unit := child.process.atomically do
  signalRaw (← get)

/-- Terminate the group before releasing its identity. Repeated calls return the saved status. -/
def Child.finish (child : Child) : IO UInt32 := child.process.atomically do
  let process ← get
  signalRaw process
  collectRaw process

end Beam.OwnedProcess
