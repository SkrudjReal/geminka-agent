FROM python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

# Copy uv binary from official Astral image
COPY --from=ghcr.io/astral-sh/uv@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation & unbuffered stdout
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1

# Install dependencies using lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy project files
COPY . .

RUN groupadd --gid 1000 geminka \
    && useradd --uid 1000 --gid 1000 --create-home geminka \
    && mkdir -p /app/data /app/memories /app/downloads \
    && chown -R geminka:geminka /app/data /app/memories /app/downloads

USER geminka

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD [".venv/bin/python", "-m", "app.healthcheck"]

CMD [".venv/bin/python", "main.py"]
