# Pub Mirror (Python Cached Proxy)

A simple cached proxy server written in Python, packaged as a Docker container.

## Features
- HTTP proxy with local caching
- Dockerized
- Persistent cache via volumes

## Quick Start (Docker)

```bash
docker run -d \
  -p 9191:9191 \
  -v ./cache:/cache \
  ghcr.io/InfyDex/pub-mirror
