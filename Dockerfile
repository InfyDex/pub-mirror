# Use official Python 3 image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy code
COPY . /app

# Copy requirements
COPY requirements.txt /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port
EXPOSE 9191

# Run your script
CMD ["python3", "proxy_cached.py", "--host", "0.0.0.0", "--port", "9191", "--cache-dir", "/app/cache/srv/pub/packages"]
