# Single-stage Python image. Build via `fly deploy` (remote builder, amd64).
#
# Image holds:
#   - apodornot package (pipeline + FastAPI service + MCP server)
#   - reference_distributions.json (3.2 MB, ships via package_data)
#
# Runtime mounts /data from a Fly volume for upload retention. ANTHROPIC_API_KEY
# / NASA_API_KEY come from Fly secrets at runtime.
#
# Single stage on purpose — the scientific Python stack (numpy/scipy/sep/photutils)
# pulls in libgomp1 + libjpeg + libtiff regardless of stage, so a multi-stage
# split saves <50 MB at the cost of extra build complexity.

ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# OS deps:
#   - libgomp1            : OpenMP runtime, used by scipy + skimage
#   - libjpeg62-turbo     : Pillow JPEG read/write
#   - libpng16-16, zlib1g : Pillow PNG read/write
#   - libtiff6            : Pillow TIFF read/write
#   - build-essential, gcc: needed when any wheel compiles from source on amd64
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      gcc \
      libgomp1 \
      libjpeg62-turbo \
      libpng16-16 \
      libtiff6 \
      zlib1g \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer caching: deps install before code copy.
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --upgrade pip \
 && pip install ".[web,mcp]"

# Default upload dir — Fly volume mounts /data here.
ENV APODORNOT_UPLOAD_DIR=/data/uploads
RUN mkdir -p /data/uploads

EXPOSE 8000

CMD ["apodornot-web", "--host", "0.0.0.0", "--port", "8000"]
