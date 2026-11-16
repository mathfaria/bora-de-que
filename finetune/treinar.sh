#!/usr/bin/env bash
# Treina o adaptador LoRA e funde no modelo base (formato Hugging Face, carregável pelo transformers).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
$PY finetune/gerar_dataset.py
$PY -m mlx_lm lora -c finetune/lora.yaml 2>&1 | tee finetune/treino.log
$PY -m mlx_lm fuse --model Qwen/Qwen2.5-0.5B-Instruct --adapter-path finetune/adapters --save-path models/bocado-extrator
cp finetune/MODEL_CARD.md models/bocado-extrator/README.md 2>/dev/null || true
echo "modelo fundido em models/bocado-extrator"
