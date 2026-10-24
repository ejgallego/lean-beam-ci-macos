# Output contract follow-ups

These are planned investigations and changes, not guarantees of the current API. The shared
presentation work in [#373](https://github.com/leanprover/lean-beam/pull/373) establishes consistent
public results and recovery data. Keep the following work independently reviewable rather than
expanding that PR into a broker or resource-lifetime redesign.

## Architecture, in priority order

- [ ] **Give stats and open-document payloads one typed schema owner.** Share result types between
  broker producers and public projection. Decode JSON at transport boundaries; remove redundant
  producer encoding and presentation decoding where the call is already typed. Preserve explicit
  global/workspace scope and test complete CLI/MCP results for both scopes. Completion means schema
  changes no longer require parallel field lists in producers and presentation code.
- [ ] **Make invalid output-operation combinations unrepresentable.** Replace combinations such
  as `.broker .stats`, which require a runtime rejection, with constructors that carry the correct
  result family and scope. Preserve all supported operations without adding public mode flags or
  inferring scope from payload shape. Completion means invalid combinations fail at construction
  rather than becoming `invalidResult` at presentation time.
- [ ] **Give speculative diagnostics explicit location provenance.** Establish which locations
  Lean can supply for document text and speculative input, then define a small typed representation
  that distinguishes those origins. Test multiline snippets and nested tactics. Record the exact
  upstream Lean dependency for unavailable information; never manufacture document ranges from
  snippet-relative offsets. Ship useful unpatched behavior separately from upstream-dependent work.
- [ ] **Measure agent-visible noise before changing MCP representations.** Capture what supported
  clients actually show from `content` and `structuredContent`, including errors, proof states and
  large diagnostic results. Report serialized bytes separately from rendered text and measured
  token exposure. Evaluate redundant workspace context and MCP output schemas against those
  measurements. Preserve supported protocol behavior and one result schema across compact/pretty
  output; do not add format modes without a concrete client need.

## Resource ownership

- [ ] **Review stream, publisher, task and retained-handle lifetimes.** Identify concrete duplicated
  cleanup or lifetime defects before introducing callback-scoped/RAII-style helpers. Preserve
  cancellation, cleanup-failure precedence, committed receipts, handle invalidation, and the rule
  that a partial stdout write must not trigger a second response. Keep the pure output projector
  free of resource-management abstractions. Completion is a targeted fix with fault-path tests,
  or a documented finding that the existing ownership scopes are sufficient.

## Agent recovery and usability

- [ ] **Expose requested diagnostic replay consistently on CLI and MCP.** Evaluate a CLI equivalent
  of MCP's `diagnostics_in_result`. Distinguish an omitted replay from a requested empty array,
  preserve bounded output, and define stdout/stderr behavior before adding the option.
- [ ] **Give stale handles an actionable recovery hint.** A bare `contentModified`/“File changed.”
  should identify the required sync and fresh `run-at-handle` step without implying the old
  speculative state can be recovered. Test edit, close/reopen and session-restart invalidation.
- [ ] **Make recovery commands portable.** Coordinate structured command/argument representations
  with [#371](https://github.com/leanprover/lean-beam/issues/371), which tracks Windows-shell remedies.
  Keep the current `recovery_plan` command-string contract until a separately reviewed replacement
  defines execution and display semantics for all supported shells; do not introduce legacy aliases.

For each item, record its owner, issue/PR and acceptance evidence when work starts. Qualification of
#373 remains separate: review the final diff, freeze its head, run projection/writer tests and Fast
and defensive Slow, repeat real CLI/MCP proof and handle probes, and require hosted checks for that
exact head before recommending merge.
