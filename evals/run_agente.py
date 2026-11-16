"""Avaliação de ponta a ponta do agente (usa a API do LLM).

Fluxo do app: pratos primeiro, lugares reais quando a pessoa pede. Cada caso roda numa das 4 capitais
com recorte congelado (em rodízio), numa conversa nova e com um visitante novo. Mede grounding (IDs
fora das ferramentas antes da trava), se o agente seguiu o "pratos primeiro", se os pratos respeitam o
pedido, se a mensagem inventou preço, e pede a um LLM juiz uma nota de 1 a 5.

Resultados por caso ficam em evals/.cache/agente/, então dá para parar quando a cota acabar e
continuar depois do mesmo ponto.

    python evals/run_agente.py --limite 40 --pausa 8
    python evals/run_agente.py --relatorio        # só consolida o que já rodou
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from bocado.agent import Agente
from bocado.avaliacao import caminho_casos, carregar_casos, percentis, salvar_resultado
from bocado.config import RAIZ, get_settings
from bocado.db import aplicar_schema, criar_pool
from bocado.embeddings import get_embedder
from bocado.llm import get_llm
from bocado.llm.base import LLMIndisponivel, LLMRespostaInvalida
from bocado.local import osm
from bocado.local.busca import TAG_EVITAR
from bocado.local.cardapio import POR_ID
from bocado.local.cep import Local

CACHE = RAIZ / "evals" / ".cache" / "agente"
REGIOES = ["sao-paulo-paulista", "belo-horizonte-savassi", "salvador-barra", "recife-centro"]

SISTEMA_JUIZ = """Você avalia respostas de um assistente que ajuda a decidir O QUE comer. Ele sugere pratos
(típicos de cada cozinha ou regionais do estado do usuário); restaurantes reais só aparecem se o usuário pedir onde
comer. Ele NÃO tem cardápio, preço, nota nem tempo de entrega. Dê uma nota de 1 a 5:
5 = pratos adequados ao pedido, respeitam restrições e vontades, mensagem clara, curta e honesta.
4 = boa, com um deslize pequeno (um prato menos alinhado, motivo vago).
3 = aceitável, mas ignora parte do pedido ou explica mal.
2 = sugestões pouco relacionadas ao pedido, ou mensagem confusa.
1 = viola uma restrição, inventa informação (preço, nota) ou não ajuda.
Quando nada atende, dizer isso com honestidade merece nota alta. Responda só com o JSON pedido."""

SCHEMA_JUIZ = {
    "type": "object",
    "properties": {"nota": {"type": "integer"}, "justificativa": {"type": "string"}},
    "required": ["nota", "justificativa"],
}


def _local(nome: str) -> Local:
    m = json.loads((RAIZ / "data" / "recortes" / f"{nome}.json").read_text(encoding="utf-8"))["meta"]
    return Local(
        cep=m["cep"],
        logradouro=m.get("logradouro", ""),
        bairro=m.get("bairro", ""),
        cidade=m["cidade"],
        uf=m["uf"],
        ibge=m.get("ibge", ""),
        lat=m["lat"],
        lon=m["lon"],
        precisao=m.get("precisao", "rua"),
    )


def julgar(llm, caso: dict, local: Local, resp) -> dict:
    pratos = [
        {
            "prato": p["nome"],
            "cozinha": p["cozinha"],
            "tags": p["tags"],
            "regional": p["regional"],
            "motivo": p["motivo"],
        }
        for p in resp.pratos
    ]
    lugares = [
        {
            "lugar": r["lugar"]["nome"],
            "cozinha": r["lugar"]["cozinha"],
            "distancia_km": r["lugar"]["distancia_km"],
            "motivo": r["motivo"],
        }
        for r in resp.recomendacoes
    ]
    entrada = json.dumps(
        {
            "pedido": caso["texto"],
            "onde": f"{local.bairro}, {local.cidade}/{local.uf}",
            "resposta": resp.mensagem,
            "pratos": pratos,
            "lugares": lugares,
        },
        ensure_ascii=False,
        indent=1,
    )
    bruto, uso, _ = llm.gerar_json(SISTEMA_JUIZ, entrada, SCHEMA_JUIZ)
    d = json.loads(bruto)
    return {"nota": max(1, min(5, int(d["nota"]))), "justificativa": d["justificativa"]}


def prato_respeita(p: dict, gold) -> bool:
    tags = set(p.get("tags") or [])
    if not set(gold.restricoes) <= tags or any(TAG_EVITAR[e] in tags for e in gold.evitar):
        return False
    if any(a not in tags for a in gold.atributos if a in ("leve", "proteico", "apimentado")):
        return False
    if gold.categorias:
        prato = POR_ID.get(p["id"])
        onde = set(prato.categorias_onde) if prato else {p["categoria"]}
        return bool(onde & set(gold.categorias))
    return True


def rodar_caso(agente: Agente, juiz, pool, caso: dict, regiao: str) -> dict:
    local = _local(regiao)
    vid = str(uuid.uuid4())
    with pool.connection() as conn:
        conn.execute("insert into visitantes (id, cep, nome) values (%s, %s, %s)", (vid, local.cep, "Avaliação"))
    resp = agente.responder(vid, local, caso["texto"], nome="Avaliação")
    linha = {
        "id": caso["id"],
        "texto": caso["texto"],
        "regiao": regiao,
        "modo": resp.modo,
        "mensagem": resp.mensagem,
        "pratos": [p["nome"] for p in resp.pratos],
        "lugares": [r["lugar"]["nome"] for r in resp.recomendacoes],
        "pratos_respeitam": [prato_respeita(p, caso["gold"]) for p in resp.pratos],
        "latencia_ms": resp.latencia_ms,
        "tokens_entrada": resp.uso.tokens_entrada,
        "tokens_saida": resp.uso.tokens_saida,
        "custo_usd": resp.custo_usd,
        "ids_invalidos_1a_tentativa": resp.metricas.get("ids_invalidos_1a_tentativa", 0),
        "ids_descartados": resp.metricas.get("ids_descartados", 0),
        "citou_preco": bool(resp.metricas.get("citou_preco")),
        "chamadas_ferramenta": resp.metricas.get("chamadas_ferramenta", 0),
        "ferramentas": [p["nome"] for p in resp.passos if p.get("tipo") == "ferramenta"],
    }
    if resp.modo == "agente":
        linha["juiz"] = julgar(juiz, caso, local, resp)
    return linha


def consolidar(linhas: list[dict]) -> dict:
    ag = [x for x in linhas if x["modo"] == "agente"]
    n = len(ag) or 1
    n_pratos = sum(len(x["pratos"]) for x in ag) or 1
    notas = [x["juiz"]["nota"] for x in ag if "juiz" in x]
    return {
        "n_casos": len(linhas),
        "n_modo_agente": len(ag),
        "respostas_com_id_inventado_1a_tentativa": sum(x["ids_invalidos_1a_tentativa"] > 0 for x in ag) / n,
        "ids_inventados_entregues": 0,  # garantido pela trava de grounding
        "seguiu_pratos_primeiro": sum(bool(x["pratos"]) and not x["lugares"] for x in ag) / n,
        "pratos_que_respeitam_o_pedido": sum(sum(x["pratos_respeitam"]) for x in ag) / n_pratos,
        "respostas_que_citam_preco": sum(x["citou_preco"] for x in ag) / n,
        "respostas_sem_sugestao": sum(not x["pratos"] and not x["lugares"] for x in ag) / n,
        "nota_juiz_media": sum(notas) / len(notas) if notas else None,
        "nota_juiz_distribuicao": {str(k): notas.count(k) for k in range(1, 6)},
        "chamadas_ferramenta_media": sum(x["chamadas_ferramenta"] for x in ag) / n,
        "latencia_ms": percentis([x["latencia_ms"] for x in ag]),
        "tokens_por_conversa": sum(x["tokens_entrada"] + x["tokens_saida"] for x in ag) / n,
        "custo_usd_por_conversa": sum(x["custo_usd"] for x in ag) / n,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--casos", default="pedidos")
    ap.add_argument("--limite", type=int, default=100)
    ap.add_argument("--pausa", type=float, default=8.0, help="segundos entre casos (respeitar o RPM do plano)")
    ap.add_argument("--refazer", action="store_true")
    ap.add_argument("--relatorio", action="store_true", help="não chama a API; só consolida o cache")
    a = ap.parse_args()

    casos = carregar_casos(caminho_casos(a.casos))[: a.limite]
    pasta: Path = CACHE / a.casos
    pasta.mkdir(parents=True, exist_ok=True)
    s = get_settings()

    if not a.relatorio:
        llm = get_llm()
        if llm is None:
            raise SystemExit("Configure GEMINI_API_KEY no .env")
        llm.espera_max_s = 70
        pool = criar_pool(s.database_url)
        emb = get_embedder()
        with pool.connection() as conn:
            aplicar_schema(conn)
            for nome in REGIOES:
                loc = _local(nome)
                conn.execute(
                    """insert into ceps (cep, logradouro, bairro, cidade, uf, ibge, lat, lon, precisao)
                                values (%s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing""",
                    (loc.cep, loc.logradouro, loc.bairro, loc.cidade, loc.uf, loc.ibge, loc.lat, loc.lon, loc.precisao),
                )
        osm.aquecer(pool, emb, [RAIZ / "data" / "recortes"])
        osm.calcular_embeddings(pool, emb)
        agente = Agente(pool, llm=llm, embedder=emb)
        for i, caso in enumerate(casos):
            arq = pasta / f"{caso['id']}.json"
            if arq.exists() and not a.refazer:
                continue
            try:
                linha = rodar_caso(agente, llm, pool, caso, REGIOES[i % len(REGIOES)])
            except (LLMIndisponivel, LLMRespostaInvalida) as e:
                print(f"parando no caso {caso['id']}: {e}")
                break
            if linha["modo"] != "agente":
                print(f"{caso['id']}: caiu no modo simples (provável cota); parando para não sujar a medição")
                break
            arq.write_text(json.dumps(linha, ensure_ascii=False, indent=1), encoding="utf-8")
            print(
                f"[{i + 1}/{len(casos)}] {caso['id']} {linha['regiao']} nota={linha.get('juiz', {}).get('nota')} "
                f"{linha['latencia_ms']} ms pratos={linha['pratos']} lugares={linha['lugares']}",
                flush=True,
            )
            time.sleep(a.pausa)

    ids = {c["id"] for c in casos}
    linhas = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pasta.glob("*.json")) if p.stem in ids]
    m = consolidar(linhas)
    meta = {
        "casos": a.casos,
        "modelo": s.gemini_model,
        "extrator": s.extractor,
        "quando": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    salvar_resultado(f"agente_{a.casos}", {"meta": meta, "metricas": m, "casos": linhas})
    print(json.dumps(m, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
