# Phase 2 — Microphone and photo inputs

**Outcome.** Employees can send an editable transcript or attach an authorized photo.

Verify speech independently of photos. Follow `deployments/speech/README.md` to
start the merged VieGem-ASR worker and AI gateway. Run the speech smoke script
with a Vietnamese WAV and reference text, and verify an unavailable upstream
produces a caller-visible failure. Use the gateway's documented speech protocol.
The worker-only multipart endpoint is outside the gateway contract. Inspect the
existing capture client for protocol compatibility before reusing it. If an
adapter is needed, keep it on the authenticated backend path and test the
gateway failure and successful transcript delivery before wiring the composer.

- Reuse the microphone capture and speech client in `frontend/src/hooks/voice-recording.ts` and `frontend/src/services/voice-api.ts` where their contracts fit. Populate the existing composer with an editable transcript. Send the final text through phase 1. Surface permission, recording, and transcription failures.
- Extend the backend chat request with an optional photo file ID. Use the file service’s authorization and content reader. Check access on every send and validate actual media type and size before forwarding. Keep browser file paths and arbitrary remote URLs out of the model request.
- Extend `agent/src/denso_agent/schemas/chat.py`, `runtime/conversation.py`, `runtime/graph.py`, and `clients/modelark.py` for bounded image input. Confirm the selected deployment accepts the intended image message format. Set image limits and compatible HTTP body limits across hops. Preserve old text requests. Keep stable attachment references in public history and decide explicitly how subsequent turns resolve prior photos. Keep raw image bytes out of public history and logs.
- Keep photo send gated on the real file API and verified provider compatibility. Test the agent and frontend with controlled dependencies in the interim.

```python
async def read_chat_photo(file_id: UUID, owner_id: UUID) -> ChatPhoto:
    """Load a size- and type-checked photo accessible to the chat owner."""
    file = await files.require_owned_photo(file_id, owner_id)
    return await files.read_bounded_photo(file)
```

- [ ] A Vietnamese recording populates editable text and the edited value is sent.
- [ ] A valid owned photo reaches a controlled provider in the expected format.
- [ ] Foreign, deleted, unsupported, and oversized photos fail before any model call.
- [ ] Text-only history remains readable. Attachment previews use authorized file access.

## Code path and first failing tests

- **Microphone.** Edit `frontend/src/hooks/workspace.tsx` to route the transcription result into the active composer. Reuse `voice-recording.ts` and `voice-api.ts` where compatible. Send through the existing text action. Test that the user can edit a returned transcript before sending and that switching chats prevents a late result from updating the new composer.
- **Photo.** Edit `backend/api/conversations.py` and `backend/src/clients/agent.py` to accept an optional photo UUID, authorize it through the files service, then forward verified MIME type and bounded content. Add the attachment fields to `agent/src/denso_agent/schemas/chat.py`. Edit `runtime/conversation.py` to checkpoint the file reference with the user message and supply image bytes through per-run context. Edit `runtime/graph.py` and `clients/modelark.py` to build the current turn's multimodal provider input from that context.
- **Initial photo policy.** Save the reference and text, and supply image bytes to the model for the attached turn. On later turns the model sees the saved text and previous answer. If the employee wants the model to inspect the image again, they reattach it. The UI can open the saved reference through the file authorization route.
- **First failing photo tests.** Extend the backend conversation tests to prove a foreign file ID produces zero agent calls. Extend `agent/tests/unit/test_modelark.py` with a controlled request asserting the image reaches the provider. Extend the persistence tests to check that the saved history contains the reference and excludes the encoded image.
