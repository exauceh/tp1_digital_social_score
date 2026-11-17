# Digital Social Score API (BiLSTM, CPU), deployed on Kubernetes (see k8s/).
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

# UV_NO_CACHE: without it the downloaded wheels stay in /root/.cache/uv inside the layer (1.3 GB on
# top of the 1.4 GB environment), which slows down every image pull when a node is added.
ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml or uv.lock change.
# --no-default-groups skips the dev and train groups (notebook, kagglehub, transformers, laya...),
# and uv picks the small CPU build of PyTorch on Linux.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --no-install-project --no-editable

COPY src ./src
RUN uv sync --frozen --no-default-groups --no-editable

# Trained artifacts: model.pt, vocab.json, metrics.json (and score_weights.json when present).
COPY models/lstm ./model

RUN useradd --create-home --uid 10001 appuser
USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    MODEL_DIR=/app/model \
    TORCH_NUM_THREADS=2

# Port 8080 is the one declared in k8s/deployment.yaml and k8s/service.yaml.
EXPOSE 8080
# --no-access-log: the uvicorn access log writes the caller's IP address (personal data) for every
# request. The API logs its own line per request, without any address (see api.py).
# --limit-concurrency 200: nothing else caps how many requests uvicorn accepts at once. The single
# lock around the model (see ScoringService.score in api.py) already serialises the real work to
# one request at a time, so queuing far more than this never helps throughput, only lets a flood
# hold ever more memory; past the limit uvicorn answers 503 instead of accepting another body.
# This is only the backstop: it counts every request, probes included, so it stays high. The
# real cap on /score (30 in flight, probes exempt) is ConcurrencyLimitMiddleware in the app.
CMD ["uvicorn", "tp1_digital_social_score.api:app", "--host", "0.0.0.0", "--port", "8080", \
     "--no-access-log", "--limit-concurrency", "200"]
