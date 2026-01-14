FROM python:3.11-slim

# Avoid Python buffering issues
ENV PYTHONUNBUFFERED=1

# Create app directory
WORKDIR /app

# Copy your script
COPY proxy_cached.py .

# Create cache directory inside container
RUN mkdir -p /cache

# Expose the port
EXPOSE 9191

# Default command
ENTRYPOINT ["python3", "proxy_cached.py"]
CMD ["--host", "0.0.0.0", "--port", "9191", "--cache-dir", "/cache"]
