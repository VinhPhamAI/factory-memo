# Product overview and requirements

**Goal:** help factory employees handle machine faults faster and pass practical
knowledge to newer employees. This is the intended product, not a list of completed features.

## Competition context

- The four-person team passed round 1 of the DENSO hackathon.
- DENSO mentors advised narrowing the story around faster fault handling and
  clearly identifying the source of knowledge.
- Hozen DX, DENSO's digital maintenance system, was suggested as a source of written
  abnormality records and images. Access and integration are not established.
- Check the [official competition website](https://densohackathon.vn/home) for
  current schedules and presentation requirements. Do not infer deadlines from old plans.

## Flow 1: create searchable knowledge

1. Upload documents, images, handwritten notes, or other supported material.
2. Extract and organize the knowledge while retaining original evidence.
3. Review the knowledge and its supporting pages, quotations, and images.
4. Search those extracted records; the UI may display them as case cards.

The target uses `knowledge_sources`, `knowledge_chunks`, and
`knowledge_chunk_sources`. There is no separate required collection of historical
incidents alongside this extracted knowledge. See the
[database proposal](../architecture/database-schema.md) for fields and relationships.

Audio knowledge ingestion is deprioritized because noisy recordings can introduce
unreliable information. This does not exclude voice interaction with the employee.

## Flow 2: investigate a fault and create an inquiry

1. An employee reports symptoms using text, voice, or photos.
2. The system searches relevant knowledge and supporting images, then suggests
   investigation or repair steps with uncertainty made clear.
3. The employee triggers Inquiry Creation through a button or a system suggestion.
4. The system composes an inquiry from the conversation: situation, observations,
   attempted actions, proposed fix, and evidence.
5. A remote expert accepts, edits, denies, or returns a question.
6. The employee replies or follows the confirmed guidance and reports the outcome.

The expert must be able to understand the inquiry without reading the full conversation.
The system supports assistance without the expert's immediate physical presence.

## Product rules

- An inquiry is not a case card or knowledge source.
- Confirming a proposed repair does not automatically create or approve knowledge.
- Employees see inquiries they created. Experts also see inquiries assigned to them.
- Inquiry access does not automatically grant access to the originating conversation.
- Separate suggested actions, completed actions, and verified outcomes.
- Do not invent machine evidence, photographs, procedures, or successful repairs.
- Draft knowledge must remain visibly distinct from expert-approved knowledge.
- Richer visual assistance, including possible 3D views, is an undecided approach.

## What exists and what remains

| Area | Status |
|---|---|
| Backend knowledge processing/search and web UI | Existing implementation; some behavior predates this scope |
| Standalone streamed answering and PostgreSQL memory | Implemented in `agent/` |
| Refined shared database schema | Proposed; migration not implemented |
| Standalone-agent retrieval, attachments, and inquiry integration | Work to implement |
| Full multimodal extraction and visual assistance | Not established by the current demo |

Verify current code before making delivery claims. Older audio flows and diagrams
may describe earlier work, not the intended priority.

## Completion criteria for the demo

- Show useful, evidence-backed assistance for one prepared fault scenario.
- Show contextful Inquiry Creation and an expert question or decision.
- Preserve knowledge evidence and inquiry responses across service restarts.
- Demonstrate the supported user experience honestly, including uncertainty and limitations.

See [system responsibilities](../architecture/system-architecture.md),
[engineering standards](../development/code-standards.md), and the
[roadmap](../development/roadmap.md) for implementation direction.
