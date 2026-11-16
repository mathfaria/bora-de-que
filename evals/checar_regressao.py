"""Falha (exit 1) se alguma métrica ficar abaixo do piso em evals/limites.json.

Só checa os arquivos de resultado que existem; use --exigir para tornar algum obrigatório.
"""

from __future__ import annotations

import argparse
import json
import sys

from bocado.avaliacao import RESULTADOS
from bocado.config import RAIZ


def pegar(d: dict, caminho: str):
    for parte in caminho.split("."):
        d = d[parte]
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exigir", nargs="*", default=[])
    a = ap.parse_args()
    limites = json.loads((RAIZ / "evals" / "limites.json").read_text(encoding="utf-8"))
    falhas = 0
    for nome, pisos in limites.items():
        if nome.startswith("_"):
            continue
        arq = RESULTADOS / f"{nome}.json"
        if not arq.exists():
            if nome in a.exigir:
                print(f"FALHA {nome}: resultado não encontrado")
                falhas += 1
            continue
        dados = json.loads(arq.read_text(encoding="utf-8"))
        base = dados if any(m.startswith("metricas.") for m in pisos) else dados.get("metricas", dados)
        for metrica, piso in pisos.items():
            valor = pegar(base, metrica)
            ok = valor is not None and valor >= piso
            falhas += not ok
            print(f"{'ok   ' if ok else 'FALHA'} {nome}.{metrica} = {valor:.3f} (piso {piso})")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
