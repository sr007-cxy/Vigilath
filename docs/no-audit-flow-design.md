# Workbench Flow Without Manual Audit Approval

The current product flow lets administrators create a customer project, configure its profile, run checks, and review results from the workbench. Customers consume reports from their dashboard.

## Roles

- **Administrator**: create projects, edit profiles, start runs, inspect progress, review generated content, and mark publication state.
- **Customer**: view health reports, scores, evidence, and published content.

## Design principles

- Long-running work is asynchronous and exposes progress and failure state.
- Every mutation is scoped by account and recorded in the audit log.
- Results remain readable without requiring an administrator approval step.
- The UI should link to the API route and service responsible for each action.

This document is a product-flow reference; the implemented routes and permission checks are authoritative.
