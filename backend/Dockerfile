# =============================================================================
# FILE: backend/Dockerfile
# WHAT: Builds the Docker image for both the FastAPI server and the worker.
#       One image, two services — docker-compose runs different CMD for each.
# WHY:  A single image means you only build once. The API and worker share the
#       same codebase and dependencies, so a shared image is correct.
# BUILD STAGES:
#   Base → deps → code
#   Deps are installed before code is copied so Docker can cache them.
#   If you only change Python files (not requirements.txt), the pip install
#   layer is reused from cache — builds go from ~5 min to ~5 seconds.
# TORCH NOTE:
#   torch on PyPI ships with CUDA support = ~2GB download.
#   We install the CPU-only wheel first (from PyTorch's own index, ~200MB).
#   When requirements.txt is processed next, pip sees torch is already installed
#   and satisfying the >=2.2.0 constraint — so it skips re-downloading it.
# =============================================================================

FROM python:3.12-slim

# Set working directory — all paths inside the container are relative to this.
# /app is the standard convention for Python web apps.
WORKDIR /app

# ── Install dependencies ───────────────────────────────────────────────────────
# Copy requirements.txt BEFORE copying the source code.
# Docker caches each RUN layer. If requirements.txt hasn't changed,
# the pip install layer is reused from cache on the next build.
# If you copy source first, any code change invalidates the pip cache.
COPY requirements.txt .

# Step 1: Install CPU-only PyTorch (must come before requirements.txt install).
# --index-url points ONLY to PyTorch's wheel index (not PyPI) so pip
# downloads the ~200MB CPU wheel, not the ~2GB CUDA wheel from PyPI.
RUN pip install --no-cache-dir \
    "torch>=2.2.0" \
    --index-url https://download.pytorch.org/whl/cpu

# Step 2: Install everything else from requirements.txt.
# torch is already installed and satisfies >=2.2.0 — pip skips it.
# --no-cache-dir keeps the image lean (no pip download cache on disk).
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application code ──────────────────────────────────────────────────────
# Done AFTER pip install so code changes don't invalidate the dep cache.
# The venv/ directory is excluded by .dockerignore — packages live in
# /usr/local/lib/python3.12/site-packages/ (the Docker Python, not the venv).
COPY . .

# ── No CMD here ───────────────────────────────────────────────────────────────
# docker-compose overrides CMD per service:
#   api    → uvicorn main:app --host 0.0.0.0 --port 8000
#   worker → python -m workers.investigation_worker
# Keeping CMD out of the Dockerfile makes the image flexible.
