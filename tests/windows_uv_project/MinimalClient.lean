import Std.Internal.UV.TCP
import Std.Internal.UV.Signal
import Std.Net.Addr

open Std.Internal.UV Std.Net

def await (p : IO.Promise (Except IO.Error α)) : IO α := do
  let some r := p.result?.get | throw (IO.userError "promise dropped")
  match r with
  | .ok x => pure x
  | .error e => throw e

def main (args : List String) : IO Unit := do
  let port := args.head!.toNat!.toUInt16
  let signal ← Signal.mk 2 false
  let pending ← Signal.next signal
  let event := pending.result?
  try
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
    discard <| IO.hasFinished event
  finally
    Signal.stop signal
