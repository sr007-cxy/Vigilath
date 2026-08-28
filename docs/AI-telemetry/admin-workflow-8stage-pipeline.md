# Admin Workflow and Automation Pipeline

The admin workbench presents an automated content and GEO pipeline as role-oriented tasks rather than a manual sequence of stages.

## Pipeline contract

1. Create or update a brand profile.
2. Generate and review query seeds.
3. Expand and classify queries.
4. Run GEO and visibility checks.
5. Draft content with provider and policy constraints.
6. Review and approve publication.
7. Publish through configured connectors.
8. Measure mentions, citations, sentiment, and outcomes.

Workers own retries and scheduling; the UI displays state, errors, and evidence. Each stage must be idempotent and account-scoped. Current API routes and service implementations take precedence over this overview.
