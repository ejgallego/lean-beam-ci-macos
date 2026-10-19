import Std.Internal.UV.TCP
import Std.Internal.UV.Signal
import Std.Net.Addr

open Std.Internal.UV Std.Net

def await (p : IO.Promise (Except IO.Error α)) : IO α := do
  let some r := p.result?.get | throw (IO.userError "promise dropped")
  match r with
  | .ok x => pure x
  | .error e => throw e

def withSignal (action : IO Unit) : IO Unit := do
  let signal ← Signal.mk 2 false
  let pending ← Signal.next signal
  let event := pending.result?
  try
    action
    discard <| IO.hasFinished event
  finally
    Signal.stop signal

def tcp (port : UInt16) : IO Unit := do
  let client ← TCP.Socket.new
  await (← TCP.Socket.connect client (SocketAddressV4.mk (.ofParts 127 0 0 1) port))
  await (← TCP.Socket.send client #["ping".toUTF8])
  let mut received := ByteArray.empty
  while received.size < 4 do
    let some chunk ← await (← TCP.Socket.recv? client (4 - received.size).toUInt64)
      | throw (IO.userError "unexpected EOF")
    received := received ++ chunk
  unless received == "pong".toUTF8 do throw (IO.userError "bad reply")
  await (← TCP.Socket.shutdown client)

def main (args : List String) : IO Unit := do
  let mode := args.head!
  let port := args[1]!.toNat!.toUInt16
  match mode with
  | "empty" => pure ()
  | "signal" => withSignal (pure ())
  | "tcp" => tcp port
  | "both" => withSignal (tcp port)
  | _ => throw (IO.userError "unknown probe mode")
