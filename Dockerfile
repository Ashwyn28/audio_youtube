FROM node:22-slim AS node-runtime

FROM python:3.11-slim AS base

WORKDIR /var/task

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

FROM base AS dev
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Metadata-only compatibility for the existing Lambda handler. Native long-lived
# audio streaming should use the production HTTP server target above.
FROM public.ecr.aws/lambda/python:3.11 AS lambda
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV AUDIO_ENABLED=false
CMD ["main.handler"]

# The ordinary/default build is the streaming HTTP application.
FROM base AS production
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
