# Lean-only Windows TCP/signal reduction

`MinimalClient.lean` is the preserved 31-line reduction from Beam commit
`15c0fd0713ad9fea5cd9495b99bf8e0cdcf5b97f`, originally `.diagnostic-probe/MinimalClient.lean`.
Its SHA256 is `18311bc7262be62d0543eba25f8d36607d739e681b50e09ff9a297955ea073ec`.
It imports only Lean's TCP, signal, and address modules; it uses no Beam plugin or native helper.

The historical Windows run completed 194 compiled executions before an unsuccessful execution.
That is an intermittent observation, not an expected failure at a fixed iteration or proof of a
particular native cause. The experimental consumer runs 256 ordinary executions and stops on the
first failure, without debugger retries.

The Lake project also builds `LibuvVersion.lean` to record and compare the compiled and interpreted
runtime's libuv version. These checks use only the selected SDK's runtime directory and Windows
System32 on PATH, so the host compiler SDK does not supply a substitute runtime DLL.

The manual workflow invokes [run-windows-lean-experiment.py](../run-windows-lean-experiment.py),
which retains the source hash, identities, each observed result, and peer settlement before invoking
the existing Beam native qualification harness. Preparing these sources does not qualify a Windows
distribution or enable bundled Beam upstream.
