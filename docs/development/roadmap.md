# Development roadmap

**Purpose:** order the remaining work by dependency and define what completion means.
This is not a delivery schedule or an assignment of work to team members.

## Foundation already available

- Existing backend knowledge processing/search and web UI.
- Standalone agent with native reload, Docker execution, streamed answers, and
  persistent PostgreSQL conversation memory.
- Reviewed target schema and shared engineering expectations.

The schema and these standards are documentation; they do not complete the features below.

## Milestones

| Milestone | Work | Completion evidence |
|---|---|---|
| Reproducible data setup | Implement reviewed backend migrations and preserve existing evidence/history | New and existing-volume setup tests; validated import and recovery procedure |
| Accounts and ownership | Add users, roles, conversation records, and access checks | Tests for allowed and denied requests across roles and owners |
| Searchable extracted knowledge | Adapt upload processing and retrieval to `knowledge_sources` and `knowledge_chunks` | Evidence links, review status, dense/hybrid search, and deletion filters verified |
| Agent integration | Connect retrieval and add history access; define attachment and evidence contracts | Controlled-provider integration tests and real service-boundary checks |
| Inquiry workflow | Compose and submit self-contained inquiries; support expert decisions and questions | Employee/expert visibility and question/reply/decision flow verified |
| Demo integration | Connect employee and expert screens; add supported voice/photo interactions | Prepared scenario works across relevant services and preserves saved records |
| Presentation readiness | Rehearse the scenario and explain limitations | Verified setup instructions, measured behavior, and current official competition requirements |

Some work can proceed independently once API contracts are clear. Do not require
a teammate's complete service to exist before writing isolated behavior tests.

## Decisions still needed

- Specific interfaces for retrieval tools and multimodal services.
- Supported image/document formats and limits.
- The richer visual experience; 3D remains an option, not a selected approach.
- Retention and permanent deletion rules beyond ordinary soft deletion.
- Conversation performance limits and whether later message paging is necessary.

## Rules for closing a milestone

- Follow the [engineering standards](code-standards.md) completion checklist.
- Record actual checks and limitations; a mocked screen or fixture alone does not
  prove an integrated feature works.
- Preserve the distinction between expert approval of one repair and approval of
  general reusable knowledge.
- Verify dates against the [official competition website](https://densohackathon.vn/home)
  when scheduling. No dates are established by this roadmap.
