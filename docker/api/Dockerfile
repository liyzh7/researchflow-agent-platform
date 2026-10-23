FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv
FROM python:3.13.16-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_NO_CACHE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Basic mode explicitly excludes the AI dependency subtree. No implicit weight download.
RUN uv export --quiet --locked --no-default-groups --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --require-hashes -r /tmp/requirements.txt
COPY README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv pip install --system --no-deps . && uv pip check --system \
    && useradd --create-home --uid 10001 app
USER app
EXPOSE 8000
# Container-private interface only; only nginx publishes a loopback host port.
CMD ["python", "-m", "uvicorn", "researchflow.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--limit-concurrency", "32", "--timeout-graceful-shutdown", "15"]
