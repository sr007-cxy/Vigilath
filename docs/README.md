# Documentation index

This directory contains architecture, operations, product, implementation, and
historical planning documents.

Status markers used in this index:

- **Maintained / English** — reviewed against the current codebase.
- **Needs translation** — retained reference material that still contains non-English prose.
- **Historical / planning** — useful context, but not an operational source of truth.

A star marks the maintained entry point when multiple documents cover the same subject.

## Architecture and engineering

| Document | Purpose | Status |
| --- | --- | --- |
| [architecture.md](architecture.md) | Star: system topology, services, data, deployment, and scaling constraints | Maintained / English |
| [url-validation-cases.md](url-validation-cases.md) | Shared frontend/backend URL validation cases | Maintained / English* |
| [i18n-status.md](i18n-status.md) | Frontend internationalization status | Maintained / English |
| [content-type-templates.md](content-type-templates.md) | Content template design | Maintained / English |
| [non-determinism-analysis.md](non-determinism-analysis.md) | Analysis of non-deterministic behavior | Maintained / English |

## Deployment and operations

| Document | Purpose | Status |
| --- | --- | --- |
| [deployment-guide.md](deployment-guide.md) | Star: production deployment, rollback, systemd, and nginx | Maintained / English |
| [performance-guide.md](performance-guide.md) | Performance diagnosis and optimization | Maintained / English |

## Product and UX

| Document | Purpose | Status |
| --- | --- | --- |
| [user-guide.md](user-guide.md) | User guide | Maintained / English |
| [self-geo-optimization.md](self-geo-optimization.md) | Vigilath self-optimization backlog | Maintained / English |

## Integrations

| Document | Purpose | Status |
| --- | --- | --- |
| [AI-telemetry](AI-telemetry/) | AI telemetry design and operations | Maintained / English |

## Reference and analysis

| Document | Purpose | Status |
| --- | --- | --- |
| [ai-cost-analysis.md](ai-cost-analysis.md) | AI provider cost and request-volume analysis | Maintained / English |
| [issue_list.md](issue_list.md) | Historical issue register and acceptance notes | Maintained / English |
| [non-determinism-analysis.md](non-determinism-analysis.md) | Sources of score variability | Maintained / English |
| [sentiment-gap-analysis-vs-wisersone.md](sentiment-gap-analysis-vs-wisersone.md) | Sentinel capability-gap analysis | Maintained / English |

## Maintenance policy

- Write new and actively maintained documentation in English.
- Never place real credentials, host passwords, tokens, cookies, or private
  infrastructure secrets in documentation.
- Add new documents to the relevant section instead of creating a flat index.
- Mark superseded documents as historical and link to the replacement.
- Translate or retire legacy Chinese documents when they next receive
  substantive updates.

\* Chinese domain names and example inputs are intentionally preserved in validation test cases.
