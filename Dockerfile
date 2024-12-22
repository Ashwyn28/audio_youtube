#########################
# Base & Dev Stage
#########################
FROM public.ecr.aws/lambda/python:3.11 as base

# Set up your working directory
WORKDIR /var/task

# Copy requirements and install dependencies (system-wide or right into /var/task)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code
COPY . .

# Create a "dev" stage that just overrides the entrypoint
FROM base as dev
RUN pip install --no-cache-dir uvicorn[standard]
# Override the Lambda entrypoint so you can run uvicorn locally
ENTRYPOINT [""]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

#########################
# Production Stage
#########################
FROM base as production
# For Lambda, keep the default entrypoint and just specify the handler
CMD ["main.handler"]

# todo: handle local/prod stages correctly