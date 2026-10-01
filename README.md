# Tasks

### Turn Detection

- [x] Write main class.
- [ ] Implement Voice Activity Detection
- [ ] Implement Semantic turn detection
- [ ] Integrate the full pipeline of Semantic Turn Detection and Voice Activity Detection into the main class.
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this pipeline.

### Voice Activity Detection 
- [ ] Write main class
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this feature.

### Text to speech
- [ ] Write main class
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this feature.

### Speech to text
- [ ] Write main class
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this feature.

# Project Structure

backend/app.py: Run backend api (not done yet)
backend/src/*: Contain main logic of seperate voice and llm service.
backend/utils/*: Contain some helper function, schema, and other thing stuff.
backend/router/*: Router for api
backend/api/*: API


# Installation & Usage
Install dependencies from the project root:
```
uv sync
```

Start PostgreSQL + pgvector:
```
docker compose -f deployments/docker-compose.yml up -d
```

Start the Knowledge Agent API:
```
uv run uvicorn backend.app:app --host 0.0.0.0 --port 8005
```

Start the Streamlit trace UI in another terminal:
```
uv run streamlit run streamlit_app.py
```

The UI selects `enterprise_rag` by default and calls
`POST /v1/knowledge-agent/enterprise_rag/stream`. Selecting another KB changes
the URL to `/v1/knowledge-agent/<kb>/stream` and starts a new conversation.
It renders the normalized SSE
events `run.started`, `tool.started`, `tool.completed`, `answer.delta`,
`answer.reset`, `answer.citations`, `run.completed`, and `run.failed`. Tool traces expose only
allowlisted inputs and summaries; raw evidence transcripts and model reasoning
are not sent to the UI.

Call the EnterpriseRAG agent directly after ingesting its documents:
```bash
curl -N http://localhost:8005/v1/knowledge-agent/enterprise_rag/stream \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"query":"What are the multipart upload limits?","thread_id":"enterprise-demo"}'
```

Reuse `thread_id` for follow-up questions. History is stored in memory per KB
and is cleared when the API restarts. The original
`POST /v1/knowledge-agent/stream` remains available with `kb` in the JSON body.
The scoped route takes only `query` and `thread_id`. Unknown KBs return 404,
invalid requests return 422, and failures after streaming starts arrive as
`run.failed` SSE events. The Streamlit connection field (or the
`KNOWLEDGE_AGENT_API_URL` environment variable) accepts the API root, the
original stream URL, or a KB-specific stream URL.

The API and the `kb` CLI use the settings documented in `.env.example`.

### Knowledge bases

See the [knowledge base guide](backend/knowledge_bases/README.md) for
creating a KB, configuring extraction and retrieval, preparing datasets,
ingesting/resuming data, and running EnterpriseRAG or AssetOpsBench.

Start the legacy Next.js frontend (not connected to the Knowledge Agent API):
```
cd frontend && npm install && npm run dev
```

### Git Command

Create a new branch
```
git checkout -b <branch-name>
```

Push to branch
```
git add .
git commit -m "feat: add feature"
git push origin <branch-name>
```

# References

https://voiceaiandvoiceagents.com/

https://arxiv.org/pdf/2501.06282

https://theneuralmaze.substack.com/p/a-hands-on-project-to-master-multimodal

https://www.youtube.com/watch?v=c19PrP3bd6Y

https://www.deeplearning.ai/short-courses/building-ai-voice-agents-for-production/
