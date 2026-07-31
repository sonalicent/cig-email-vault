# Email `.eml` → Markdown/JSON pipeline

An S3-triggered AWS Lambda that converts Outlook `.eml` emails landing under
`emails/<date>/` in the `be-cig-vault-ds-raw` bucket into human- and RAG-friendly
**Markdown** (plus a **JSON** metadata sidecar) under `emails-extracted/`.

## What it does

For each new `.eml` object:

1. **Parse** the MIME message (headers, best body, attachment parts) with the stdlib
   `email` package.
2. **Body selection** — prefer a non-empty `text/plain` part; otherwise convert the
   `text/html` part to Markdown via BeautifulSoup4 + markdownify.
3. **Thread split** — a direct email yields one unit; a forward yields the wrapper
   (dropped if empty) plus the quoted emails, split on Outlook divider lines and quoted
   `From:/Sent:/To:/Subject:` header blocks. `EXTERNAL EMAIL` banners are preserved.
4. **Dedup** — compute an identity (RFC 5322 `Message-ID`, else a SHA-256 of normalized
   From+Date+Subject+body). A DynamoDB conditional `PutItem` (`attribute_not_exists`)
   enforces first-writer-wins idempotency.
5. **Attachments** — only parts with `Content-Disposition: attachment` are extracted
   (inline signature/logo images are skipped). Dispatch by type: pdf → pdfplumber,
   pptx → python-pptx, xlsx → openpyxl, docx → python-docx, txt/csv → decode.
6. **Write** each email and attachment as Markdown (+ JSON) under date-partitioned
   `emails-extracted/content/<date>/` and `emails-extracted/attachments/<date>/`.

Trigger path: **S3 → SQS (+ DLQ) → Lambda**.

## Documentation

- [docs/index.md](docs/index.md) — documentation index.
- [docs/architecture.md](docs/architecture.md) — concepts, AWS topology, and end-to-end flow.
- [docs/components.md](docs/components.md) — module-by-module reference.
- [docs/design-decisions.md](docs/design-decisions.md) — the D1–D26 decision record.

## Layout

```
data-pipeline/
  pyproject.toml
  Dockerfile                     # public.ecr.aws/lambda/python:3.12
  docs/
    design-decisions.md          # D1–D26 ADR record
    decision-confirmations.md    # Q&A of options offered vs. selected
  src/email_pipeline/
    handler.py                   # Lambda entry: SQS->S3 event -> orchestrate
    config.py                    # bucket/prefixes/table/queue names, env overrides
    s3_io.py                     # get_object, put_markdown, put_json
    eml_parser.py                # stdlib email: headers, best body, attachment parts
    html_to_md.py                # BeautifulSoup4 + markdownify
    thread_splitter.py           # split body into individual emails
    dedup.py                     # identity hash + DynamoDB conditional PutItem
    formatter.py                 # render unit -> Markdown (+ JSON metadata)
    metrics.py                   # CloudWatch EMF custom metrics
    extractors/                  # pdf / pptx / xlsx / docx / text dispatch
   tests/                         # pytest suite + synthetic .eml fixtures
   infra/cdk/                     # CDK deployment stack
```

## Configuration (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `SOURCE_BUCKET` | `be-cig-vault-ds-raw` | Bucket holding input and output objects |
| `INPUT_PREFIX` | `emails/` | Prefix scanned for `.eml` inputs |
| `OUTPUT_PREFIX` | `emails-extracted/` | Prefix for generated Markdown/JSON |
| `DEDUP_TABLE` | `cig-vault-email-dedup` | DynamoDB dedup table name |
| `METRICS_NAMESPACE` | `CigVault/EmailPipeline` | CloudWatch metrics namespace |
| `MAX_ATTACHMENT_BYTES` | `26214400` (25 MiB) | Skip attachments larger than this |

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

## Deployment

The CDK app in `infra/cdk/` deploys the pipeline as:

`S3 ObjectCreated (emails/*.eml) -> SQS ingest queue -> container Lambda -> DynamoDB dedup table`

It also creates a DLQ and CloudWatch alarms for DLQ depth and Lambda errors.

```powershell
cd infra/cdk
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cdk synth
cdk deploy
```

The defaults match the runtime configuration table above. Override deployment context as
needed, for example:

```powershell
cdk deploy -c sourceBucketName=be-cig-vault-ds-raw -c region=us-west-2
```

## Deferred (later pass)

- `scripts/backfill.py` — one-time enqueue of pre-existing `emails/<date>/*.eml`.
- Security hardening (SSE-KMS, parser sandboxing, in-VPC) per D25.
