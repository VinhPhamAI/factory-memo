# Deployment and documentation verification

Date: 2026-11-02. No paid provider calls were made.

## Implemented artifacts

- Independent frozen image, running as user `agent`.
- Compose overlay with an explicit setup job and runtime-only credential mapping.
- Native/container environment examples with separate setup and runtime files.
- Package README, API contract, development notes, and a short root README link.
- Independent GitHub Actions workflow using the package configuration explicitly.
- Real image, native reload, and documented request tests.

## Test evidence

| Command / behavior | Result |
|---|---|
| Deployment contract tests before artifact creation | Exit 1, two expected missing-artifact failures. |
| Deployment contract tests after artifact creation | Exit 0, two passed. |
| `docker build -t local/denso-agent:dev agent` | Exit 0; frozen install succeeded. |
| First actual Compose end-to-end test | Exit 0, one passed in 181.62 seconds. |
| Latest complete end-to-end attempt before Windows reload correction | Exit 1, four passed and one reload failure in 177.33 seconds. |
| Documented curl and PowerShell requests | Passed against the controlled provider and actual native CLI. |
| Installed CLI reload after the Windows supervisor correction | Exit 0, one passed in 25.31 seconds. |
| Complete end-to-end suite after the correction | Exit 0, five passed in 130.37 seconds. |

The passing image test covers a native completed turn, stopping native serving,
starting the actual image with unchanged environment files, preserved context,
container restart, setup rerun, and runtime secret isolation. It inspects that
only PostgreSQL, setup, and agent services exist in the unique test project.

A later passing image run also covers a controlled first-fragment barrier over
real TCP and a client disconnect followed by successful fresh input for the same
conversation. No timing threshold substitutes for the provider barrier.

Windows reload initially detected the temporary source edit but waited forever
for the serving child to stop. The locked Uvicorn supervisor sends a console
Ctrl+C event and then joins without a timeout. A private hidden console and
restoring the parent Ctrl+C handler did not resolve the failure. The test uses a
temporary editable copy, preserves repository source, and removes its process
tree. The installed CLI now passes the reload test: its serving process changes,
completed memory survives, repository source remains unchanged, and the port is
released. All five end-to-end tests subsequently passed together using ordinary
hidden background processes and the current application image.

The independent debugger confirmed a real console in the child and successful
Ctrl+C-handler restoration; neither fixed the locked supervisor's behavior.
[Microsoft's console-event documentation](https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent)
states that a Ctrl+C event addressed to a nonzero process group can succeed
without being delivered. A narrow Windows-only supervisor now uses bounded
terminate/kill waits, refuses replacement while the old worker remains alive,
and closes sockets on failure. Three helper tests pass, including a worker that
does not stop. The helper passes Pylint. The actual installed CLI reload proof
also passes; no fixture-level application bypass remains. Final end-to-end
verification uses ordinary hidden background processes without special console
handling.

The final coordinating run also includes a later container assertion for an
upstream connection ending after its first delta, followed by fresh input for
the same conversation. The provider barrier now ends unsuccessfully if its
deadline expires; it never sends a successful remainder without test release.
Those final-run results are recorded separately by the coordinating session.

The native/container test leaves both environment files byte-identical and uses
their respective explicit database addresses. Its controlled provider is on the
host: native requests use loopback and image requests use
`host.docker.internal`. This routing difference belongs only to the test fixture;
ordinary ModelArk configuration uses the same external URL in both modes.

All container cleanup uses explicitly named, unique test projects. No developer
volume or backend container was removed. Runtime receives no setup-owner or
bootstrap URL. The examples contain placeholders and dedicated test credentials.

## Read-only commit preparation

- `git diff --check` passed.
- `AGENTS.md` and `CLAUDE.md` remained byte-identical.
- `agent/.env` and `agent/.env.setup` are ignored.
- `agent/uv.lock` is included by the narrow ignore exception.
- No populated environment file appeared among untracked files.
- A scan for provider key patterns, private keys, and long literal Bearer secrets
  found no matches in the new service, deployment overlay, workflow, or current
  plan. Example placeholders and explicit test-only credentials were reviewed.
- Existing guidance changes and older planning work were preserved.
- No staging, commit, push, or pull request action was performed.

Suggested implementation commit title: `feat(agent): add standalone streaming service`.
Final commit readiness depends on the complete green suites and independent code
review recorded by the coordinating session.
