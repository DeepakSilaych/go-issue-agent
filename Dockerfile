# go-issue-agent — self-contained image.
# Based on the Go toolchain so the agent runs `go build/test/vet` natively inside the
# container (no Docker-in-Docker needed); Python runs the agent itself.
FROM golang:1.25-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# App source (indexes/, prompts/, rules/, agent/, solve.py, …) — see .dockerignore.
COPY . .
RUN chmod +x docker-entrypoint.sh

# solve.py defaults to ./workspace and ./output (= /app/workspace, /app/output);
# mount a host dir over /app/output to collect results.
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["--help"]
