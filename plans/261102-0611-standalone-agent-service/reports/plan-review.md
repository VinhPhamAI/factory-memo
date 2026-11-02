# Independent plan review

Scope: official plan, technical design, tests, phase files and task ledger. This review changes no implementation or plan decisions. All implementation phases remain pending.

## Findings

### P1 — Switching from native development to Docker requires undefined URL edits

- Source: `technical-design.md:60-62`, `technical-design.md:81-88`, `technical-design.md:247`.
- Native commands load runtime/setup files whose database URLs use `localhost:5434`. The container command reads those same files, while the design requires container URLs using `postgres:5432`. The sentence requiring explicit configuration before the command does not supply a repeatable switch. Both files also declare `AGENT_DATABASE_URL`; the later interpolation file can overwrite the earlier value.
- Correction: retain native URLs for CLI operation and define named container connection variables in the same example files. Map them explicitly to the standard application variables in each Compose service. For example, the runtime service maps `AGENT_DATABASE_URL: ${AGENT_CONTAINER_DATABASE_URL}`. The setup job similarly maps its three container URLs. Document exactly which file owns each variable. No file edit or arbitrary hostname rewriting should be required when changing mode.
- Proof: run setup and a conversation natively, stop native serving, then start the container with the documented command and the same unchanged configuration files. Verify both modes reach the intended database and that no administrative variables enter runtime.

### P2 — The accumulated-answer limit is required but not specified

- Source: `technical-design.md:49-58`, `technical-design.md:163`, `technical-design.md:180`; `phase-03-graph-and-streaming.md:23`.
- The design calls `check_answer_bound` and requires a bounded answer buffer, but gives no unit, default or limit variable. Implementers cannot write a shared boundary test until those values are chosen. The provider's token limit does not bound a malformed test stream or an incompatible provider response.
- Correction: select a named character or UTF-8-byte cap and document it beside the request/output settings. A fixed `MAX_ANSWER_CHARACTERS = 65536` is sufficient for this version. Check before appending or streaming the next fragment. Crossing the cap emits `output_limit`, closes upstream and never produces completion.
- Proof: test exactly-at-limit success and one-character-over-limit failure with a controlled provider, then verify stream closure and recovered admission capacity.

### P2 — Folder and marker selection can omit tests without detecting the omission

- Source: `test-plan.md:14`, `test-plan.md:105-110`; `phase-01-package-and-cli.md:36`.
- Commands select suite directories and also require matching suite markers. The plan registers markers but does not specify assigning them to each test module. Unmarked tests in a partially marked directory are omitted while the remaining tests pass.
- Correction: either use the directory paths alone for the three suite commands, or require a module-level `pytestmark` and a collection check that every non-live test belongs to exactly one suite. Directory-only commands are simpler. Keep the explicit `live` marker and environment opt-in for paid tests.
- Proof: add an ordinary test in each suite directory and confirm the documented command collects it without additional tagging. Record collection and nonzero exit status on actual test failure.

## Review outcome

The service boundary, shared-secret trust model, separate setup/runtime credentials, one-instance restriction and direct SDK/custom-stream approach are internally consistent. The issues above should be corrected before handoff. Library failure-state behavior remains an explicitly required integration proof rather than an assumed guarantee. No further architectural layer or service is needed.

## Resolution verification

The revised planning documents were read after corrections. All three findings are resolved at design level:

- Native/container switching now uses explicit named container connection variables and per-service mappings. Runtime receives the container runtime URL; setup receives its bootstrap, owner and runtime provisioning URLs. The acceptance test requires switching modes with unchanged files and the same persisted conversation.
- The answer cap is now `MAX_ANSWER_CHARACTERS = 32_000`, measured with Python `len` across fragments. It is checked before appending or emitting. Below/at/over-limit tests require stream closure, capacity recovery and no completion after overflow. The selected value differs from the review example but resolves the missing contract.
- The three suite commands now select directories without redundant suite-marker filters. The separate paid `live` marker and opt-in remain. Ordinary unmarked tests in suite directories are included.

The task ledger also separates parent phases from prerequisites. A critical task no longer depends on completion of its own parent. The revised prerequisite graph has no evident cycle; phases complete after their critical tasks pass.

No unresolved actionable design finding remains from this review. Runtime compatibility, provider behavior, actual checkpoint recovery, native reload and container operation remain implementation tests. No application tests were executed or claimed by this document review. All implementation phases remain pending.
