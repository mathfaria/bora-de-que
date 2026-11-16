"""Avalia um extrator de filtros contra o conjunto de referência.

python evals/run_extracao.py --extrator rules
python evals/run_extracao.py --extrator finetuned
python evals/run_extracao.py --extrator llm            # usa a API (cota)
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from bocado.avaliacao import (
    caminho_casos,
    carregar_casos,
    comparar_campos,
    metricas_extracao,
    percentis,
    salvar_resultado,
)
from bocado.config import get_settings
from bocado.extraction import get_extrator
from bocado.extraction.schema import Filtros
from bocado.llm.base import LLMIndisponivel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extrator", required=True, choices=["rules", "llm", "finetuned", "base"])
    ap.add_argument("--casos", default="pedidos")
    ap.add_argument("--pausa", type=float, default=0.0, help="segundos entre chamadas (respeitar RPM da API)")
    a = ap.parse_args()

    casos = carregar_casos(caminho_casos(a.casos))
    ext = get_extrator(a.extrator)
    if a.extrator == "llm":
        ext._c.espera_max_s = 70  # na avaliação vale esperar a janela de cota por minuto
    linhas, pares, latencias = [], [], []
    tok_in = tok_out = invalidos = 0
    for i, c in enumerate(casos, 1):
        try:
            r = ext.extrair(c["texto"])
        except LLMIndisponivel as e:
            print(f"interrompido no caso {c['id']}: {e}")
            break
        # Na produção, JSON inválido cai nas regras. Aqui conta como resposta vazia,
        # senão a nota do modelo misturaria o acerto das regras.
        pred = r.filtros if r.valido else Filtros()
        pares.append((c["gold"], pred))
        latencias.append(r.latencia_ms)
        tok_in += r.tokens_entrada
        tok_out += r.tokens_saida
        invalidos += not r.valido
        cmp = comparar_campos(c["gold"], pred)
        linhas.append(
            {
                "id": c["id"],
                "texto": c["texto"],
                "gold": c["gold"].compacto(),
                "pred": pred.compacto(),
                "valido": r.valido,
                "bruto": r.bruto,
                "erros": [k for k, ok in cmp.items() if not ok],
                "latencia_ms": round(r.latencia_ms, 1),
            }
        )
        if i % 10 == 0:
            print(f"{i}/{len(casos)}")
        if a.pausa:
            time.sleep(a.pausa)

    m = metricas_extracao(pares)
    n = len(pares) or 1
    s = get_settings()
    m.update(
        {
            "json_valido": 1 - invalidos / n,
            "latencia_ms": percentis(latencias),
            "tokens_entrada_medio": tok_in / n,
            "tokens_saida_medio": tok_out / n,
            "custo_usd_por_1000": (
                (tok_in * s.preco_entrada_por_mtok + tok_out * s.preco_saida_por_mtok) / 1e6 / n * 1000
                if a.extrator == "llm"
                else 0.0
            ),
        }
    )
    meta = {
        "extrator": a.extrator,
        "casos": a.casos,
        "quando": datetime.now(UTC).isoformat(timespec="seconds"),
        "modelo": getattr(ext, "modelo", None) or getattr(ext, "caminho", None),
    }
    caminho = salvar_resultado(f"extracao_{a.extrator}_{a.casos}", {"meta": meta, "metricas": m, "casos": linhas})
    print(
        f"acerto exato {m['acerto_exato']:.1%} | média dos campos {m['acerto_medio_campos']:.1%} | "
        f"JSON válido {m['json_valido']:.1%} | p50 {m['latencia_ms']['p50']:.0f} ms -> {caminho}"
    )


if __name__ == "__main__":
    main()
