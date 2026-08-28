# Content Types and Template Design

This document describes the template model used by the content-generation workflow.

## Design

A brand profile may select multiple creation directions and copywriting types. The selected combination determines the prompt template and the output metadata. Templates should be deterministic, auditable, and easy to extend without changing the API contract.

## Template requirements

- Include audience, objective, tone, language, source material, and factual constraints.
- Separate system instructions from user-provided content.
- Preserve the target query and brand profile identifiers in generated records.
- Return structured output that can be reviewed before publication.
- Reject unsupported combinations with a clear validation error.

## Implementation guidance

Keep template definitions close to the content-generation service and cover each template with a fixture or integration test. Do not place provider keys, customer data, or unpublished credentials in templates.
