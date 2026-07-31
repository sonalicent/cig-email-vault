# Container image for the email .eml -> Markdown/JSON Lambda.
# Base: AWS-provided Python 3.12 Lambda runtime image (D2, D3).
FROM public.ecr.aws/lambda/python:3.12

# Install dependencies first for better layer caching.
COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY src/ ${LAMBDA_TASK_ROOT}/src/

# Install the package (and its runtime deps) into the Lambda task root.
RUN python -m pip install --no-cache-dir "${LAMBDA_TASK_ROOT}"

# Handler entrypoint: module.function
CMD ["email_pipeline.handler.handler"]
