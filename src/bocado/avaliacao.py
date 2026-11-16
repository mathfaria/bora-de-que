"""Funções de métrica compartilhadas pelos scripts de avaliação e pelos testes."""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any

from bocado.config import RAIZ
from bocado.extraction.schema import CAMPOS, Filtros
from bocado.textos import sem_acento

CASOS = RAIZ / "evals" / "cases" / "pedidos.jsonl"
RESULTADOS = RAIZ / "evals" / "results"


def caminho_casos(nome: str) -> Path:
    return RAIZ / "evals" / "cases" / f"{nome}.jsonl"


def carregar_casos(caminho: Path = CASOS) -> list[dict[str, Any]]:
    casos = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            c = json.loads(linha)
            c["gold"] = Filtros.model_validate(c["filtros"])
            casos.append(c)
    return casos


def comparar_campos(gold: Filtros, pred: Filtros) -> dict[str, bool]:
    g, p = gold.model_dump(), pred.model_dump()
    return {c: g[c] == p[c] for c in CAMPOS}


def metricas_extracao(pares: list[tuple[Filtros, Filtros]]) -> dict[str, Any]:
    por_campo = {c: 0 for c in CAMPOS}
    exatos = 0
    for gold, pred in pares:
        cmp = comparar_campos(gold, pred)
        for c, ok in cmp.items():
            por_campo[c] += ok
        exatos += all(cmp.values())
    n = len(pares) or 1
    return {
        "n": len(pares),
        "acerto_exato": exatos / n,
        "acerto_por_campo": {c: v / n for c, v in por_campo.items()},
        "acerto_medio_campos": sum(por_campo.values()) / (n * len(CAMPOS)),
    }


def percentis(valores: list[float]) -> dict[str, float]:
    if not valores:
        return {"p50": 0.0, "p95": 0.0}
    v = sorted(valores)
    p95 = v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]
    return {"p50": statistics.median(v), "p95": p95}


def casa_alvo(prato: dict[str, Any], alvo: list[str]) -> bool:
    texto = sem_acento(f"{prato['nome']} {prato['descricao']}")
    return all(any(re.search(rf"(?<![a-z]){re.escape(alt)}", texto) for alt in item.split("|")) for item in alvo)


def salvar_resultado(nome: str, dados: dict[str, Any]) -> Path:
    RESULTADOS.mkdir(parents=True, exist_ok=True)
    caminho = RESULTADOS / f"{nome}.json"
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return caminho
