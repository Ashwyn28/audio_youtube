FROM public.ecr.aws/lambda/python:3.11 AS base

WORKDIR /var/task

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run Uvicorn instead of the Lambda entrypoint during local development.
FROM base AS dev
ENTRYPOINT []
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS production
CMD ["main.handler"]
