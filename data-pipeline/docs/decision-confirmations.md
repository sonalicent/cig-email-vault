# Design Decision Confirmations — Email .eml → text Lambda pipeline

Record of the clarifying questions asked during planning and the user's selected answers.
Original date: 2026-07-27. Purpose: revisit/confirm scope later.

## Planning-phase confirmations

| # | Question | Options offered | Selected |
|---|----------|-----------------|----------|
| 1 | Lambda packaging — how to package given heavy pdf/pptx/xlsx parser deps? | Container image Lambda (ECR) *(recommended)* / Zip + Lambda Layers | **Container image Lambda (ECR)** |
| 2 | Dedup index store — where to keep the "already-processed email" registry to avoid re-saving emails seen in earlier threads? | DynamoDB table *(recommended)* / S3 manifest JSON | **S3 manifest JSON** → later **revised to DynamoDB table** (see D6) |
| 3 | IaC tooling — how to define infra (Lambda, trigger, IAM, table)? | AWS SAM *(recommended)* / AWS CDK (Python) / Terraform / Code only (no IaC) | **AWS CDK (Python)** |
| 4 | Input format — will actual S3 inputs be real .eml (MIME) with binary attachments (samples in cigvault are .txt exports)? | Yes, real .eml/MIME files *(recommended)* / Mixed / unsure | **Yes, real .eml/MIME files** |
| 5 | Language runtime — confirm Python? | Python 3.12 *(recommended)* / Other | **Python 3.12** |

## Implementation-phase confirmations (this pass)

| # | Question | Options offered | Selected |
|---|----------|-----------------|----------|
| 6 | Test fixtures — real samples contain PII. How to create fixtures? | Synthetic sanitized fixtures *(recommended)* / Copy real samples | **Synthetic sanitized fixtures** |
| 7 | Scope of this run — implement all phases? | Everything (Phases 0–4) *(recommended)* / Application only (Phases 0–2 + tests) | **Application only (Phases 0–2 + tests)** |
| 8 | May I create a venv + install deps to run pytest? | Yes / No | **Yes** |

Notes:
- Planning choices for #2 and #3 differed from the recommended option (chose S3 manifest
  over DynamoDB — later revised to DynamoDB; and CDK over SAM).
- Full rationale/alternatives for each captured in `design-decisions.md`.
- Per #7, `infra/cdk/` and `scripts/backfill.py` are deferred to a later pass.
