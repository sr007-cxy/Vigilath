# Documentation index

This directory contains architecture, operations, product, implementation, and
historical planning documents.

A star marks the maintained entry point when multiple documents cover the same
subject. Legacy documents are retained for context and may still be written in
Chinese.

## Architecture and engineering

| Document | Purpose |
| --- | --- |
| [architecture.md](architecture.md) | Star: system topology, services, data, deployment, and scaling constraints |
| [url-validation-cases.md](url-validation-cases.md) | Shared frontend/backend URL validation cases |
| [i18n-status.md](i18n-status.md) | Frontend internationalization status |
| [content-type-templates.md](content-type-templates.md) | Content template design |
| [non-determinism-analysis.md](non-determinism-analysis.md) | Analysis of non-deterministic behavior |

## Deployment and operations

| Document | Purpose |
| --- | --- |
| [deployment-guide.md](deployment-guide.md) | Star: production deployment, rollback, systemd, and nginx |
| [cloudflare-migration-plan.md](cloudflare-migration-plan.md) | Cloudflare migration and rollback plan |
| [performance-guide.md](performance-guide.md) | Performance diagnosis and optimization |
| [performance-report-2026-04-17.md](performance-report-2026-04-17.md) | Latest dated performance snapshot in this revision |

## Product and UX

| Document | Purpose |
| --- | --- |
| [PRODUCT-implemented-features](PRODUCT-%E5%B7%B2%E5%AE%9E%E7%8E%B0%E5%8A%9F%E8%83%BD.md) | Implemented feature inventory |
| [user-guide.md](user-guide.md) | User guide |
| [SSR_PLAN.md](SSR_PLAN.md) | Server-side rendering plan |
| [ssg-home-plan.md](ssg-home-plan.md) | Home-page static generation plan |
| [self-geo-optimization.md](self-geo-optimization.md) | Vigilath self-optimization backlog |

## Integrations

| Document | Purpose |
| --- | --- |
| [moltspay-integration-plan.md](moltspay-integration-plan.md) | MoltsPay integration plan |
| [moltspay-x402-browser-integration.md](moltspay-x402-browser-integration.md) | Browser-side x402 integration |
| [AI-telemetry](AI-telemetry/) | AI telemetry design and operations |
| [playwright](playwright/) | Browser-engine design and operational notes |

## Maintenance policy

- Write new and actively maintained documentation in English.
- Never place real credentials, host passwords, tokens, cookies, or private
  infrastructure secrets in documentation.
- Add new documents to the relevant section instead of creating a flat index.
- Mark superseded documents as historical and link to the replacement.
- Translate or retire legacy Chinese documents when they next receive
  substantive updates.
