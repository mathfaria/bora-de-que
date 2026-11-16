"""Avalia a busca sobre lugares REAIS (recortes congelados do OSM de 4 capitais), sem LLM.

Cada um dos 100 pedidos é rodado nas 4 regiões (400 buscas). Métricas no top-3:
- categoria_ok@3: fração dos lugares cuja cozinha bate com a pedida (casos com categoria);
- restricoes_ok@3: fração dos lugares com a restrição confirmada ou provável E prato sugerido compatível;
- evitar_ok@3: fração dos pratos sugeridos sem o item a evitar;
- alvo@3: em pedidos que citam um prato/ingrediente ("camarão", "ramen"), fração de buscas em que algum
  dos 3 lugares sugere um prato que casa com o alvo;
- sem_resultado: fração de buscas sem nenhum lugar.
Os filtros vêm do gabarito ou das previsões salvas por run_extracao.py.

    python evals/run_busca.py --filtros gold
    python evals/run_busca.py --filtros finetuned
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from bocado.avaliacao import RESULTADOS, caminho_casos, carregar_casos, casa_alvo, salvar_resultado
from bocado.config import RAIZ, get_settings
from bocado.db import aplicar_schema, criar_pool
from bocado.embeddings import get_embedder
from bocado.extraction.schema import Filtros
from bocado.local import osm
from bocado.local.busca import TAG_EVITAR, Origem, buscar_lugares
from bocado.local.cep import FUSO_POR_UF

REGIOES = ["sao-paulo-paulista", "belo-horizonte-savassi", "salvador-barra", "recife-centro"]
AGORA = datetime(2026, 11, 16, 20, 0)  # segunda, 20h: horário fixo para a avaliação ser reproduzível


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filtros", default="gold", help="gold ou nome do extrator (rules, finetuned, llm, base)")
    ap.add_argument("--casos", default="pedidos")
    a = ap.parse_args()

    casos = carregar_casos(caminho_casos(a.casos))
    if a.filtros != "gold":
        salvo = json.loads((RESULTADOS / f"extracao_{a.filtros}_{a.casos}.json").read_text(encoding="utf-8"))
        preds = {c["id"]: Filtros.model_validate(c["pred"]) for c in salvo["casos"]}
        casos = [c for c in casos if c["id"] in preds]

    pool = criar_pool(get_settings().database_url)
    emb = get_embedder()
    with pool.connection() as conn:
        aplicar_schema(conn)
    osm.aquecer(pool, emb, [RAIZ / "data" / "recortes"])
    osm.calcular_embeddings(pool, emb)

    regioes = {}
    for nome in REGIOES:
        m = json.loads((RAIZ / "data" / "recortes" / f"{nome}.json").read_text(encoding="utf-8"))["meta"]
        regioes[nome] = Origem(m["lat"], m["lon"], m["uf"], FUSO_POR_UF.get(m["uf"], "America/Sao_Paulo"))

    cat_ok = cat_n = rest_ok = rest_n = evit_ok = evit_n = alvo_ok = alvo_n = vazios = total = 0
    linhas = []
    with pool.connection() as conn:
        for c in casos:
            gold: Filtros = c["gold"]
            f = gold if a.filtros == "gold" else preds[c["id"]]
            for nome, origem in regioes.items():
                r = buscar_lugares(conn, emb, f, c["texto"], origem, limite=3, agora=AGORA)
                total += 1
                if not r.lugares:
                    vazios += 1
                for lugar in r.lugares:
                    if gold.categorias:
                        cat_n += 1
                        cat_ok += bool(set(lugar["categorias"]) & set(gold.categorias))
                    sugestao = lugar["sugestoes"][0] if lugar["sugestoes"] else None
                    if gold.restricoes:
                        rest_n += 1
                        dieta = {k: v for k, v in (lugar.get("dieta_status") or {}).items()}
                        rest_ok += all(dieta.get(x) in ("confirmado", "costuma ter") for x in gold.restricoes) and (
                            sugestao is not None and set(gold.restricoes) <= set(sugestao["tags"])
                        )
                    if gold.evitar and sugestao is not None:
                        evit_n += 1
                        evit_ok += not any(TAG_EVITAR[e] in sugestao["tags"] for e in gold.evitar)
                if c.get("alvo"):
                    alvo_n += 1
                    alvo_ok += any(
                        casa_alvo({"nome": s["nome"], "descricao": s["descricao"]}, c["alvo"])
                        for lugar in r.lugares
                        for s in lugar["sugestoes"]
                    )
                linhas.append(
                    {
                        "id": c["id"],
                        "regiao": nome,
                        "lugares": [
                            f"{x['nome']} ({x['cozinha']}) -> {x['sugestoes'][0]['nome'] if x['sugestoes'] else '-'}"
                            for x in r.lugares
                        ],
                    }
                )

    def frac(a_, b_):
        return a_ / b_ if b_ else None

    m = {
        "categoria_ok@3": frac(cat_ok, cat_n),
        "restricoes_ok@3": frac(rest_ok, rest_n),
        "evitar_ok@3": frac(evit_ok, evit_n),
        "alvo@3": frac(alvo_ok, alvo_n),
        "sem_resultado": vazios / total,
        "buscas": total,
        "regioes": REGIOES,
    }
    print(json.dumps(m, ensure_ascii=False, indent=1))
    meta = {
        "filtros": a.filtros,
        "casos": a.casos,
        "embedding": emb.nome,
        "quando": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    salvar_resultado(f"busca_{a.filtros}_{a.casos}", {"meta": meta, "metricas": m, "casos": linhas})


if __name__ == "__main__":
    main()
