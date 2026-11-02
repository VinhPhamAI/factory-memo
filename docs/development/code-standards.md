# Engineering standards

**Applies to new and changed work across the repository.** These are the team's
expectations, not a claim that all existing code already meets them. Improve the
code touched by a task; do not rewrite unrelated services to match a template.

Read [project guidance](../../AGENTS.md), the relevant service guide, and the
[product requirements](../features/project-overview.md) before implementation.

## 1. Keep responsibilities clear

- Name files, functions, and classes after their purpose. Avoid vague names such
  as `manager`, `helper`, or `utils` when a specific name is available.
- Keep functions and modules focused. Split them when responsibilities diverge,
  not to satisfy an arbitrary line count or one-class-per-file rule.
- Separate request handling, input/output definitions, application behavior,
  database access, external clients, configuration, and CLI commands as needed.
- Keep database queries and provider-specific behavior out of request handlers.
- Use ordinary functions and constructors to pass dependencies where that makes
  behavior testable. Do not introduce a generic framework for a single use case.
- Add a class when it owns meaningful state or behavior, not merely to group functions.
- Prefer explicit data flow over hidden global clients or mutable request state.

A new Python service can start with this arrangement and add folders only as needed:

| Location | Responsibility |
|---|---|
| `src/<package>/app.py` | Create the application and own startup/shutdown |
| `api/` | Authenticate, validate requests, and call application behavior |
| `schemas/` | Request, response, and event definitions |
| `services/` or a specific domain folder | Application behavior |
| `clients/` and/or `storage/` | External services and database access |
| `config.py`, `cli.py` | Settings and development commands |
| `tests/unit`, `tests/integration`, `tests/e2e` | Checks at different boundaries |

This is a starting point, not a required folder tree for every service.

## 2. Make code readable

- Use Python `snake_case` for functions/modules and `PascalCase` for classes.
  Follow the established TypeScript naming conventions in the affected package.
- Add type annotations to new Python function boundaries and meaningful return
  values. Define request and response fields explicitly.
- Use names and short comments to explain decisions. Do not narrate obvious code.
- Handle expected errors specifically. Do not hide failures with broad exception
  handling or return a successful-looking result after a failed operation.
- Keep useful diagnostics internally; return clear errors without credentials,
  private source content, or model reasoning.

### Python docstrings

New or modified functions and classes need concise, purpose-specific docstrings.
Start with what the operation does. Include meaningful `Args:`, `Returns:`, and
`Raises:` sections only where relevant. Explain value meaning and failure conditions;
do not repeat the signature or add empty sections.

```python
def normalize_message(message: str) -> str:
    """Remove surrounding whitespace and reject an empty chat message.

    Args:
        message: Text submitted by the caller.

    Returns:
        Message text with surrounding whitespace removed.

    Raises:
        ValueError: The message contains no non-whitespace characters.
    """
    cleaned = message.strip()
    if not cleaned:
        raise ValueError("A message is required.")
    return cleaned
```

A short class docstring should explain what it owns. Document constructor arguments
or operational methods where their meaning needs explanation.

## 3. Develop services independently

- Keep each standalone service's dependencies, lockfile, tests, configuration
  examples, and setup instructions together.
- Communicate through documented APIs. Do not import another service's internal
  modules; the standalone agent must not import `backend.*`.
- Support native development commands and source reload without rebuilding Docker.
- Use the same application factory and behavior in native and container execution.
- Make local/container address differences explicit. Switching modes should not
  require repeatedly editing credentials or maintaining different application code.
- Validate required settings at startup. CLI help must work without credentials
  and must not open database or provider connections.
- Give clients, connections, streams, and background work clear startup/shutdown
  owners. Close them on success, failure, timeout, cancellation, and disconnection.
- Bound request time, concurrent work, and accumulated output. Document limits and
  actual deployment assumptions; do not imply unsupported multi-instance operation.

## 4. Define APIs before wiring services together

- Specify endpoint, authentication, fields, types, limits, responses, and errors.
- Keep authentication checks in one place. The backend checks user identity and
  record ownership; a service token authenticates service-to-service calls.
- Keep service tokens separate from user login tokens and provider API keys.
  Never embed service secrets in browser code.
- For streams, define event names, ordering, completion, failure, and disconnect behavior.
- For the agent, `complete.answer` is authoritative only after generation and final
  checkpoint saving succeed. Clients replace provisional text with this answer.
- Do not claim duplicate protection, retry safety, replay, or result recovery unless
  implemented and tested. A saved result and delivery of its response can fail separately.
- Preserve published contracts. Explain compatibility changes and update callers,
  tests, and documentation together when a contract changes.

## 5. Make storage setup repeatable

- Separate database setup from serving requests. New developers must be able to
  reproduce setup through checked-in commands, not oral instructions.
- Use versioned changes for application-owned tables. Let framework-owned tables,
  such as LangGraph checkpoints, use their supported setup process.
- Keep setup-owner credentials separate from restricted runtime credentials.
  Do not pass database administration credentials into a serving container.
- Preserve data when setup runs again. Test both a new database and an existing volume.
- Document migration, compatibility, and recovery steps for storage changes.
- Preserve original evidence, review history, and uncertainty. Expert confirmation
  of an inquiry does not automatically approve reusable knowledge.
- Treat uploaded and retrieved content as evidence, not instructions to the agent.
- Keep credentials in ignored local files. Commit placeholder examples only;
  never print secrets in logs, tests, reports, or tool output.

The [schema proposal](../architecture/database-schema.md) defines the intended
storage direction. It is not permission to change running schemas in unrelated tasks.

## 6. Test behavior before implementation

1. Add a meaningful test for new behavior or a reproduced defect and observe it fail.
2. Implement the smallest clear change that passes it.
3. Refactor with tests passing, then run the checks relevant to affected boundaries.

| Test type | What it proves | Normal dependencies |
|---|---|---|
| Unit | A focused behavior or decision | No network, Docker, paid API, or shared database |
| Integration | Components work with real storage or a controlled HTTP provider | Disposable PostgreSQL or local provider server as needed |
| End-to-end | A caller can use the real service boundary | Running native service or container and controlled dependencies |

- Test failure behavior and resource cleanup as well as successful responses.
- For streaming changes, prove content arrives before the provider finishes, and
  verify completion follows successful saving.
- Test native reload, restart, container execution, and setup reruns when those
  behaviors change. Do not rerun unrelated infrastructure checks without a reason.
- Use controlled providers in normal tests. Paid calls require explicit opt-in.
- Keep fake providers in tests, not as hidden production fallbacks.
- Test externally meaningful behavior rather than duplicating implementation lines.
  Do not add tests for wording-only documentation or trivial reversible changes.
- Use explicit coordination for concurrent tests; avoid sleeps that assume scheduling.
- Missing infrastructure is not a pass. Report what ran, failed, or was skipped.
  A passing rerun does not explain an intermittent failure.
- Use each service's configured formatter, lint checks, and test commands. New tools
  need an explicit implementation change; these standards do not install them.

See the [agent development guide](../../agent/docs/development.md),
[frontend guide](../../frontend/README.md), and [project guidance](../../AGENTS.md)
for current commands. They differ by service.

## 7. Write documentation for the next reader

- Use concise, impersonal, professional English. Lead with the task or answer.
- Make guides understandable without the originating chat. Define necessary terms
  when first used; remove jargon that can be replaced with familiar words.
- Use numbered steps for procedures, tables for fields/types, and bullets for
  relationships, such as “A knowledge base contains many knowledge chunks.”
- Keep paragraphs short. Remove repeated explanations and presentation-style prose.
- Preserve technical meaning, limitations, and useful examples when shortening text.
- Label implemented behavior, proposals, and undecided ideas separately.
- Document prerequisites, command working directory, credentials required, exact
  payload/response examples, errors, and how to verify success where relevant.
- Keep detailed service setup with the service; link rather than duplicate it.
- Update affected documentation in the same change as behavior or contract changes.

## 8. Finish with evidence

Before calling work complete:

- Confirm requested behavior and relevant failure cases are covered.
- Run appropriate tests, formatting, lint, and build checks for the affected service.
- Check configuration, credentials, compatibility, and database-change implications.
- Update affected docs and examples; verify local links and command accuracy.
- Remove test-owned resources while preserving developer data and unrelated changes.
- Run `git diff --check`; keep `AGENTS.md` and `CLAUDE.md` identical.
- Report actual results, remaining limitations, and pre-existing failures plainly.
- Follow the task's authorization for commits, pushes, PRs, and publishing. Do not
  infer permission from these standards. Do not add AI coauthor attribution.

These are review expectations. This documentation change does not add automated
checks or imply that every existing package already satisfies the standard.
