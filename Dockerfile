# Base image for both local and AWS Lambda
FROM python:3.11-slim AS base

# Install necessary system packages
RUN apt-get update && apt-get install -y \
    curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code to the container
COPY . .

# --- Development Stage ---
FROM base AS dev

# Install additional tools for development
RUN pip install --no-cache-dir uvicorn[standard]

# Command for local development
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- AWS Lambda Stage ---
FROM public.ecr.aws/lambda/python:3.11 AS lambda

# Copy dependencies and application code from base image
COPY --from=base /app ${LAMBDA_TASK_ROOT}

# Set the CMD to the AWS Lambda handler
CMD ["main.handler"]
