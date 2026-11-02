# Local setup verification

Date: 2026-11-02. The developer environment is prepared; the ModelArk API key
remains deliberately blank. No paid provider request was made.

## Prepared configuration

- `agent/.env` contains a generated service token, matching runtime database
  URLs, the Singapore provider base URL and the documentation's example model
  ID, `dola-seed-2-1-turbo-260628`.
- `agent/.env.setup` contains matching generated owner/runtime passwords and
  the local Compose bootstrap URLs. Administrative values are kept separate
  from runtime settings.
- Both files are ignored by Git. Secret values were not printed or added to
  this report. Existing configuration files were not overwritten.
- Model access for the user's project remains unverified until its key is
  supplied. The example model can be replaced with an authorized endpoint ID.

## Commands and results

Commands were run from the repository root with the independent frozen lock.

| Operation | Result |
|---|---|
| `uv sync --project agent --frozen --group dev` | Passed; 74 packages audited. |
| Start Docker Desktop | Passed; Linux engine available. |
| `docker compose -f deployments/docker-compose.yml up -d --wait postgres` | Passed; PostgreSQL healthy. |
| `uv run --project agent --frozen --env-file agent/.env.setup denso-agent setup-db` | Passed. |
| Repeat setup | Passed without resetting the database. |
| Restricted runtime connection | Confirmed database `agent_memory`, role `agent_runtime`, checkpoint readiness. |
| Native `denso-agent serve --port 8010 --reload` | Started successfully. |
| HTTP health and authenticated readiness | Passed. |
| Readiness without a service token | Returned 401. |
| Two streamed conversation turns against local PostgreSQL | Completed; the second model input included the first turn. |
| Cleanup | Temporary service stopped; only its synthetic conversation deleted. |
| Git ignore checks | Both local credential files ignored. |
| Remaining Compose services | Only PostgreSQL running. |

The native HTTP check temporarily replaced provider settings in the child
process with a controlled local HTTP server and a dummy key. The saved runtime
configuration still points to BytePlus and has an empty provider key. This check
does not establish live ModelArk authentication or model availability.

The developer Compose stack was absent when preparation began. Compose created
its PostgreSQL container and named volume. No existing volume was removed, and
no backend migration or seed operation was run.

## Final step

Set `MODELARK_API_KEY` in the ignored `agent/.env` file. Then start the service:

```powershell
uv run --project agent --env-file agent/.env denso-agent serve --reload
```

A short live request can then verify the key, project permissions and selected
model. Local service readiness alone does not verify those provider settings.

## Documentation checked

- [BytePlus overview](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/product-overview)
- [Chat text generation](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/text-generation)
- [Streaming output](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/streaming-output)
- [Model list and regional base URLs](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/model-list)
- [API key project permissions](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/api-key)

The overview demonstrates the Responses API, while the text-generation and
streaming guides also document the existing Chat API used by this service.
No provider API migration was required for this preparation.
