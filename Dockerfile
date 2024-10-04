# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir fastapi uvicorn requests pytest httpx pytest-asyncio python-dotenv slowapi mangum
# Make port 80 available to the world outside this container
EXPOSE 80

# Define environment variable
ENV PYTHONUNBUFFERED=1


# Run app.py when the container launches
CMD ["uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "80"]
