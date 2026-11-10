# Digital Social Score API (BiLSTM, CPU), deployed on Kubernetes (see k8s/).
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
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
CMD ["uvicorn", "tp1_digital_social_score.api:app", "--host", "0.0.0.0", "--port", "8080"]
