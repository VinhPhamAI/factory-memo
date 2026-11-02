# Provider access diagnosis

Date: 2026-11-02.

The initial live request reached the agent and passed its service-token check.
Direct provider probes reproduced HTTP 404 with the provider code `ModelNotOpen`
for both the original Dola model and `glm-5-2-260617`. Both probes were refused
before generation. No application defect was established by these responses.

The ignored `agent/.env` now selects `MODELARK_MODEL=glm-5-2-260617`. The API key
was not changed. No application source changed, so the application test suites
were not rerun for this configuration-only update. Successful live GLM generation
has not yet been verified.

## Required account step

Enable GLM 5.2 in the user's ModelArk account through
[model activation](https://ai.byteplus.com/ark/region:ap-southeast-1/openManagement),
using the project associated with the API key. The activation page is linked
from the official
[quick-start guide](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/quick-start).

After activation, fully stop and restart the agent server:

```powershell
uv run --project agent --env-file agent/.env denso-agent serve --reload
```

The existing curl payload and service-token header remain valid. Source reload
does not reload values that `uv --env-file` placed in the parent process, so
editing `.env` requires a full command restart.

## Credential and repository checks

- `agent/.env` and `agent/.env.setup` are ignored by Git and are not tracked.
- This report contains no API key, service token, account identifier or raw
  provider response body.
- No commit, push or pull request was created.

The next live check must confirm that the activated model returns streamed text
and a final `complete` event. `ModelNotOpen` must not be described as successful
model access.
