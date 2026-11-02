# Documentation

Start with [product requirements](features/project-overview.md),
[engineering standards](development/code-standards.md), and the
[current codebase map](development/codebase-summary.md).

| Section | Contents |
|---|---|
| [Architecture](architecture/architecture.html) | System structure, knowledge flow, and database design |
| Features | Knowledge management, document uploads, conflict review, and voice capture |
| Development | Deployment, evaluation references, and demo instructions |
| [Assets](assets/) | Images used by documentation |

## Architecture

- [System responsibilities](architecture/system-architecture.md) — intended boundaries and current limitations.
- [Earlier system diagram](architecture/architecture.html)
- [Earlier architecture](architecture/architecture-v0.html)
- [Knowledge flow](architecture/knowledge-flow.html)
- [Database schema proposal](architecture/database-schema.md) — target tables, relationships, permissions, and migration requirements; not implemented.

## Features

- [Product overview and requirements](features/project-overview.md)

- [Knowledge management](features/knowledge-management.md)
- [Document uploads](features/document-uploads.md)
- [Industrial QA](features/industrial-qa.md)
- [Conflict detection](features/conflict-detection.md)
- [Voice capture](features/voice-capture.md)

## Development

- [Engineering standards](development/code-standards.md)
- [Codebase map](development/codebase-summary.md)
- [Development roadmap](development/roadmap.md)
- [Deployment services placeholder](development/deployment-services.md) — guide not yet written.
- [Hosted deployment reference](development/hosted-deployment.html)
- [AssetOpsBench data profile](development/assetopsbench-data-profile.md)
- [MR-JE-AX167 demo script](development/demo-script-mr-je-ax167.md)

## Service guides

- [Agent setup](../agent/README.md), [API](../agent/docs/api.md), and [development](../agent/docs/development.md)
- [Frontend](../frontend/README.md)
- [Speech service](../deployments/speech/README.md)
- [Knowledge-base implementation](../backend/knowledge_bases/README.md)

[Engineering standards](development/code-standards.md) define expectations for new
and changed work. [AGENTS.md](../AGENTS.md) and [CLAUDE.md](../CLAUDE.md) are matching
entry points for agent sessions.
