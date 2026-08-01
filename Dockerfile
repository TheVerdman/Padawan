FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system padawan && useradd --system --gid padawan --create-home padawan
WORKDIR /opt/padawan

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY padawan ./padawan
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN python -m pip install --no-cache-dir . && \
    mkdir -p /var/lib/padawan/artifacts && \
    chown -R padawan:padawan /var/lib/padawan

USER padawan
ENTRYPOINT ["padawan"]
CMD ["--help"]
