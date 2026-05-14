# Vibe Seller — containerised dev/test environment.
#
# Mirrors the self-hosted CI runner: Python 3.11, Node 20,
# Claude CLI, Playwright, browser-use.
#
# Usage:
#   docker compose up --build          # server on :7777
#   docker compose run --rm app bash   # interactive shell
#   docker compose down -v             # cleanup

FROM python:3.11-slim AS base

# System deps: git, curl, procps (pgrep), node, lsof, rsync
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl procps ca-certificates gnupg lsof rsync \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
       | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] \
             https://deb.nodesource.com/node_20.x nodistro main" \
       > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude CLI (agent runtime)
RUN npm install -g @anthropic-ai/claude-code

# uv — fast Python package manager
RUN pip install --no-cache-dir uv

# Playwright: bake system deps + browser binary into image layer.
# This avoids a 180 MB download on every container start.
# Browsers go to /opt/pw-browsers (readable by non-root user).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers

# Non-root user — Claude CLI refuses bypassPermissions as root.
# Entrypoint runs as root (for chown), then drops to this user
# for the server + tests via gosu.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash vibe \
    && mkdir -p /app \
    && chown vibe:vibe /app

# Git identity (tests & workspace commits)
RUN git config --global user.email "docker@vibe-seller.test" \
    && git config --global user.name "Docker Dev"

WORKDIR /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 7777
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["bash"]
