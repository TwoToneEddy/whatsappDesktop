FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable \
    && .venv/bin/python -c "from neonize.client import NewClient" \
    && uv cache clean

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["wat", "--data-dir", "/data"]
