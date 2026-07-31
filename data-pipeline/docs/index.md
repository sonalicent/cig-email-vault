# Data Pipeline Documentation

Documentation for the email `.eml` → Markdown/JSON pipeline. Start with the overview in
the [README](../README.md), then use the guides below.

## Guides

| Document | What it covers |
|----------|----------------|
| [architecture.md](architecture.md) | High-level concepts, AWS topology, end-to-end flow, idempotency, output layout, error handling, observability, and security. |
| [components.md](components.md) | Module-by-module reference for every part of `src/email_pipeline/` and the CDK stack. |

## Decision records

| Document | What it covers |
|----------|----------------|
| [design-decisions.md](design-decisions.md) | The D1–D26 decision record: context, decision, rationale, and alternatives for each choice. |
| [decision-confirmations.md](decision-confirmations.md) | Q&A of the options offered versus the ones selected. |
| [verification-checklist.md](verification-checklist.md) | End-to-end checklist for validating the pipeline. |

## Quick orientation

- **Trigger:** a new `.eml` under `emails/<date>/` in S3.
- **Path:** S3 → SQS (+ DLQ) → Lambda → DynamoDB dedup → S3 output.
- **Output:** Markdown + JSON under `emails-extracted/content/<date>/` and
  `emails-extracted/attachments/<date>/`.
- **Code:** [src/email_pipeline/](../src/email_pipeline/) · **Infra:**
  [infra/cdk/](../infra/cdk/) · **Tests:** [tests/](../tests/).
