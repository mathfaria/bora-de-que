"""Publica a demo num Hugging Face Space (Docker).

O Space precisa de um README com cabeçalho YAML próprio; este script monta uma cópia do
projeto com esse README e envia. Os segredos (DATABASE_URL, GEMINI_API_KEY) são configurados
nas Settings do Space, nunca no código.

    hf auth login
    python scripts/publicar_space.py --space SEU_USUARIO/bocado --modelo SEU_USUARIO/bocado-extrator
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

RAIZ = Path(__file__).resolve().parents[1]
INCLUIR = ["Dockerfile", ".dockerignore", "pyproject.toml", "LICENSE", "src", "data"]

CABECALHO = """---
title: bora de quê?
emoji: 🔍
colorFrom: pink
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Sugere pratos e mostra lugares reais perto do seu CEP
---

# bora de quê?

Demo do agente que sugere pratos e mostra lugares reais perto do seu CEP. Código, avaliações e decisões: {repo}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True)
    ap.add_argument("--modelo", default="", help="id do modelo de extração no Hub (opcional)")
    ap.add_argument("--repo-url", default="")
    a = ap.parse_args()

    api = HfApi()
    api.create_repo(a.space, repo_type="space", space_sdk="docker", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp)
        for item in INCLUIR:
            origem = RAIZ / item
            if origem.is_dir():
                shutil.copytree(origem, destino / item, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(origem, destino / item)
        (destino / "README.md").write_text(CABECALHO.format(repo=a.repo_url or ""), encoding="utf-8")
        if a.modelo:
            df = (destino / "Dockerfile").read_text(encoding="utf-8")
            (destino / "Dockerfile").write_text(
                df.replace('ARG FINETUNED_MODEL=""', f'ARG FINETUNED_MODEL="{a.modelo}"'), encoding="utf-8"
            )
        api.upload_folder(folder_path=str(destino), repo_id=a.space, repo_type="space", commit_message="deploy da demo")
    print(f"enviado: https://huggingface.co/spaces/{a.space}")
    passos = "Agora, em Settings do Space, configure os segredos DATABASE_URL e GEMINI_API_KEY e a variável REPO_URL"
    if a.modelo:
        passos += f", e as variáveis EXTRACTOR=finetuned e FINETUNED_MODEL={a.modelo}"
    print(passos + ".")


if __name__ == "__main__":
    main()
