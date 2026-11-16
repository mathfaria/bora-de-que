"""Publica o modelo fundido no Hugging Face Hub com o cartão preenchido pelos resultados reais.

hf auth login                      # uma vez
python finetune/publicar_modelo.py --repo SEU_USUARIO/bocado-extrator --repo-url https://github.com/SEU_USUARIO/bocado
"""

from __future__ import annotations

import argparse
import json
import re

from huggingface_hub import HfApi

from bocado.avaliacao import RESULTADOS
from bocado.config import RAIZ

MODELO = RAIZ / "models" / "bocado-extrator"


def tabela() -> str:
    linhas = ["| Extrator | Acerto exato | Média por campo | JSON válido | Latência p50 |", "|---|---|---|---|---|"]
    for ext, rotulo in (("rules", "Regras (baseline)"), ("finetuned", "Este modelo"), ("llm", "Gemini via API")):
        arq = RESULTADOS / f"extracao_{ext}_pedidos.json"
        if arq.exists():
            m = json.loads(arq.read_text(encoding="utf-8"))["metricas"]
            linhas.append(
                f"| {rotulo} | {m['acerto_exato']:.1%} | {m['acerto_medio_campos']:.1%} | "
                f"{m['json_valido']:.1%} | {m['latencia_ms']['p50']:.0f} ms |"
            )
    return "\n".join(linhas)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="usuario/nome no Hugging Face")
    ap.add_argument("--repo-url", default="")
    ap.add_argument("--tempo-treino", default="~20 min")
    a = ap.parse_args()

    log = RAIZ / "finetune" / "treino.log"
    iters = "500"
    if log.exists():
        achados = re.findall(r"^\s*(\d+)\s+[\d.]+\s", re.sub(r"\x1b\[[0-9;]*m", "", log.read_text()), flags=re.M)
        iters = achados[-1] if achados else iters
    card = (RAIZ / "finetune" / "MODEL_CARD.md").read_text(encoding="utf-8")
    card = (
        card.replace("{{MODEL_ID}}", a.repo)
        .replace("{{REPO_URL}}", a.repo_url or "#")
        .replace("{{TABELA}}", tabela())
        .replace("{{ITERS}}", iters)
        .replace("{{TEMPO_TREINO}}", a.tempo_treino)
    )
    (MODELO / "README.md").write_text(card, encoding="utf-8")

    api = HfApi()
    api.create_repo(a.repo, repo_type="model", exist_ok=True)
    api.upload_folder(
        folder_path=str(MODELO),
        repo_id=a.repo,
        repo_type="model",
        commit_message="bocado-extrator: modelo fundido + cartão",
    )
    print(f"publicado: https://huggingface.co/{a.repo}")


if __name__ == "__main__":
    main()
