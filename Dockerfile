FROM python:3.12-slim-bookworm

WORKDIR /app

# Root Dockerfile for platforms like Zeabur that auto-detect Docker projects
# from the repository root. It mirrors the existing docker/Dockerfile used by
# docker compose so both deployment paths stay aligned.

# Latest releases available at https://github.com/aptible/supercronic/releases
ARG TARGETARCH
ENV SUPERCRONIC_VERSION=v0.2.39

RUN set -ex && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    case ${TARGETARCH} in \
    amd64) \
    export SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64; \
    export SUPERCRONIC_SHA1SUM=c98bbf82c5f648aaac8708c182cc83046fe48423; \
    export SUPERCRONIC=supercronic-linux-amd64; \
    ;; \
    arm64) \
    export SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-arm64; \
    export SUPERCRONIC_SHA1SUM=5ef4ccc3d43f12d0f6c3763758bc37cc4e5af76e; \
    export SUPERCRONIC=supercronic-linux-arm64; \
    ;; \
    *) \
    echo "Unsupported architecture: ${TARGETARCH}"; \
    exit 1; \
    ;; \
    esac && \
    echo "Downloading supercronic for ${TARGETARCH} from ${SUPERCRONIC_URL}" && \
    for i in 1 2 3; do \
        echo "Download attempt $i/3"; \
        if curl -fsSL --connect-timeout 30 --max-time 60 -o "$SUPERCRONIC" "$SUPERCRONIC_URL"; then \
            echo "Download successful"; \
            break; \
        else \
            echo "Download attempt $i failed"; \
            if [ $i -eq 3 ]; then \
                echo "All download attempts failed"; \
                exit 1; \
            fi; \
            sleep 2; \
        fi; \
    done && \
    echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - && \
    chmod +x "$SUPERCRONIC" && \
    mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" && \
    ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic && \
    supercronic -version && \
    apt-get remove -y curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install dependencies first to maximize layer cache reuse
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy the app and install the project
COPY docker/manage.py .
COPY trendradar/ ./trendradar/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

COPY docker/entrypoint.sh /entrypoint.sh.tmp
RUN sed -i 's/\r$//' /entrypoint.sh.tmp && \
    mv /entrypoint.sh.tmp /entrypoint.sh && \
    chmod +x /entrypoint.sh && \
    chmod +x manage.py && \
    mkdir -p /app/config /app/output

# Bundle Zeabur-ready default config files into the image so a GitHub-based
# deployment can boot without manually creating config files in the platform UI.
COPY config/ai_filter/ /app/config/ai_filter/
COPY config/ai_analysis_prompt.txt /app/config/ai_analysis_prompt.txt
COPY config/ai_interests.txt /app/config/ai_interests.txt
COPY config/ai_translation_prompt.txt /app/config/ai_translation_prompt.txt
COPY cus_files/cus_web_ui/ /app/cus_files/cus_web_ui/
COPY cus_files/config.yaml /app/config/config.yaml
COPY cus_files/timeline.yaml /app/config/timeline.yaml
COPY cus_files/frequency_words.txt /app/config/frequency_words.txt

ENV PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/app/config/config.yaml \
    FREQUENCY_WORDS_PATH=/app/config/frequency_words.txt \
    CUSTOM_WEB_UI_ENABLED=true \
    CUSTOM_WEB_UI_TITLE="Engineer News Radar"

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
