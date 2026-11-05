# LLM service (Qwen3.5-4B on vLLM)

The `ai-llm` worker in [`deployments/ai.compose.yml`](../ai.compose.yml) runs
`Qwen/Qwen3.5-4B` on the stock `vllm/vllm-openai` server: text only (image and
video inputs disabled), with tool calling and reasoning parsing. Nothing is
wrapped, so requests and responses are vLLM's own.

## Endpoints

| Access | Base URL | Notes |
| --- | --- | --- |
| Direct | `http://<host>:8043/v1` | Port `LLM_PORT`, bound to `LLM_BIND` (default `0.0.0.0`, public) |
| Gateway | `http://127.0.0.1:8080/v1` | Same paths and bodies, streaming included; adds `x-ai-service: llm` |

Gateway paths forwarded unchanged: `/v1/chat/completions`, `/v1/completions`,
`/v1/responses`, `/v1/messages`, `/tokenize`, `/detokenize`. `GET /v1/models`
on the gateway merges all workers' models (each entry has `service`); use the
direct port for vLLM's exact `/v1/models` body.

```sh
bash deployments/ai-services-api/up.sh llm
curl http://127.0.0.1:8043/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "Qwen/Qwen3.5-4B",
  "messages": [{"role": "user", "content": "Xin chào"}],
  "tools": [{"type": "function", "function": {"name": "get_time", "parameters": {"type": "object", "properties": {}}}}]
}'
```

Backend use: `ANSWER_BASE_URL=http://ai-services-api:8080/v1`,
`ANSWER_MODEL=Qwen/Qwen3.5-4B` (same for `EXTRACTION_*`, `VERIFY_*`).

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `LLM_MODEL` | `Qwen/Qwen3.5-4B` | Model and served name |
| `LLM_PORT` / `LLM_BIND` | `8043` / `0.0.0.0` | Direct host port |
| `LLM_API_KEY` | empty | If set, vLLM requires `Authorization: Bearer <key>`, also through the gateway |
| `LLM_GPU_ID` | `0` | GPU used |
| `LLM_GPU_MEM_UTIL` | `0.30` | Share of GPU memory (about 14 GB on a 46 GB A40) |
| `LLM_MAX_MODEL_LEN` | `32768` | Context length |
| `LLM_TOOL_PARSER` | `qwen3_coder` | vLLM tool-call parser; Qwen3.5 emits `<tool_call><function=...>` XML (`hermes` returned empty `tool_calls`) |
| `LLM_REASONING_PARSER` | `qwen3` | Splits thinking into `reasoning_content`; send `chat_template_kwargs: {"enable_thinking": false}` to skip thinking |
| `LLM_HF_CACHE` | `/mnt/data/llm/hf` | Model download cache |

## Limitations

- Port 8043 is public and unauthenticated unless `LLM_API_KEY` is set.
- The text-only flag was not verified against this model;
  check the startup log and one tool-call request before relying on them.
- The first start downloads the model and can take several minutes.
