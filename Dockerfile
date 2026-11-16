# Imagem da demo: FastAPI + embeddings ONNX em CPU.
# Pensada para o Hugging Face Spaces (porta 7860, usuário 1000), roda em qualquer host de contêiner.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/hf \
    PORT=7860

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY data ./data

# O modelo de extração ajustado é opcional (o padrão é o extrator por regras).
# Só com FINETUNED_MODEL definido a imagem leva PyTorch (versão CPU) e baixa o modelo do Hub.
ARG FINETUNED_MODEL=""
RUN if [ -n "$FINETUNED_MODEL" ]; then \
      pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.3" && \
      pip install -e ".[modelo]" && \
      python -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1])" "$FINETUNED_MODEL"; \
    else \
      pip install -e .; \
    fi

# Baixa o modelo de embedding durante o build: a primeira requisição não espera download.
RUN python -c "from bocado.embeddings import get_embedder; get_embedder().embed(['ok'])"

RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/saude')"
CMD ["sh", "-c", "uvicorn bocado.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
