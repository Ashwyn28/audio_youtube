###################
# Base Stage
###################
FROM python:3.11-slim AS base

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

###################
# Dev Stage
###################
FROM base AS dev

# Install uvicorn or other dev tools
RUN pip install --no-cache-dir uvicorn[standard]

# Override ENTRYPOINT to avoid the Lambda entrypoint problem
ENTRYPOINT [""]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

###################
# Production (Lambda) Stage
###################
FROM public.ecr.aws/lambda/python:3.11 AS lambda

# Copy your code and installed packages from base
COPY --from=base /app /var/task

# The Lambda image’s entrypoint expects the first arg to be your handler
CMD ["main.handler"]
