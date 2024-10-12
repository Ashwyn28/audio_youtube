# Use the official AWS Lambda Python 3.11 base image
FROM public.ecr.aws/lambda/python:3.11

# Install necessary packages
RUN pip install --no-cache-dir fastapi uvicorn requests pytest httpx pytest-asyncio python-dotenv slowapi mangum boto3

# Copy application code to the container
COPY . ${LAMBDA_TASK_ROOT}

# Set environment variable for unbuffered logs
ENV PYTHONUNBUFFERED=1

# Set the CMD to your handler (specify module and handler)
CMD ["main.handler"]