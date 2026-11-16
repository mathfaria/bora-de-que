"""Linha de comando: bocado init-db | local | buscar | chat | serve."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid


def _pool():
    from bocado.config import get_settings
    from bocado.db import aplicar_schema, criar_pool

    s = get_settings()
    if not s.database_url:
        sys.exit("DATABASE_URL não configurada (veja .env.example)")
    pool = criar_pool(s.database_url)
    with pool.connection() as conn:
        aplicar_schema(conn)
    return pool


def _local(pool, cep: str):
    from bocado.embeddings import get_embedder
    from bocado.local import cep as cep_mod
    from bocado.local import osm

    with pool.connection() as conn:
        local = cep_mod.resolver(cep, conn)
    area = osm.garantir_area(pool, get_embedder(), local.lat, local.lon)
    return local, area


def cmd_init_db(a: argparse.Namespace) -> None:
    from bocado.db import resetar

    pool = _pool()
    if a.reset:
        with pool.connection() as conn:
            resetar(conn)
        print("esquema recriado (cache de lugares apagado)")
    else:
        print("esquema aplicado")


def cmd_aquecer(a: argparse.Namespace) -> None:
    from bocado.config import RAIZ
    from bocado.embeddings import get_embedder
    from bocado.local import osm

    pool = _pool()
    emb = get_embedder()
    resumo = osm.aquecer(pool, emb, [RAIZ / "data" / "recortes", RAIZ / "data" / "aquecimento"])
    print(f"{len(resumo)} recortes, {sum(r['lugares'] for r in resumo)} lugares; calculando embeddings…")
    print(f"embeddings: {osm.calcular_embeddings(pool, emb)}")


def cmd_reclassificar(a: argparse.Namespace) -> None:
    from bocado.embeddings import get_embedder
    from bocado.local import osm

    pool = _pool()
    print(f"{osm.reclassificar(pool)} lugares mudaram de categoria; recalculando embeddings…")
    print(f"embeddings: {osm.calcular_embeddings(pool, get_embedder())}")


def cmd_local(a: argparse.Namespace) -> None:
    pool = _pool()
    local, area = _local(pool, a.cep)
    print(json.dumps(local.dict() | {"area": area}, ensure_ascii=False, indent=1))


def cmd_buscar(a: argparse.Namespace) -> None:
    from bocado.embeddings import get_embedder
    from bocado.extraction import get_extrator
    from bocado.local.busca import Origem, buscar_lugares

    pool = _pool()
    local, _ = _local(pool, a.cep)
    filtros = get_extrator(a.extrator).extrair(a.texto).filtros
    print(f"{local.rotulo} | filtros: {filtros.compacto()}")
    with pool.connection() as conn:
        r = buscar_lugares(
            conn, get_embedder(), filtros, a.texto, Origem(local.lat, local.lon, local.uf, local.fuso), limite=a.limite
        )
    for aviso in r.avisos:
        print("aviso:", aviso)
    for x in r.lugares:
        aberto = {True: "aberto", False: "fechado", None: "?"}[x["aberto_agora"]]
        print(
            f"  {x['distancia_km']:4.1f} km  {aberto:7s} {x['nome']} ({x['cozinha'] or 'cozinha não informada'})"
            f"  dieta={x['dieta_status']}  -> {[s['nome'] for s in x['sugestoes']]}"
        )


def cmd_chat(a: argparse.Namespace) -> None:
    from bocado.agent import Agente

    pool = _pool()
    local, area = _local(pool, a.cep)
    visitante = str(uuid.uuid4())
    with pool.connection() as conn:
        conn.execute("insert into visitantes (id, cep) values (%s, %s)", (visitante, local.cep))
    agente = Agente(pool)
    conversa_id = None
    print(f"Você está em {local.rotulo} ({area['n_lugares']} lugares mapeados por perto). Ctrl+C para sair.\n")
    try:
        while True:
            msg = input("você> ").strip()
            if not msg:
                continue
            r = agente.responder(visitante, local, msg, conversa_id)
            conversa_id = r.conversa_id
            print(f"\nbora de quê? [{r.modo}]> {r.mensagem}")
            for rec in r.recomendacoes:
                lugar, prato = rec["lugar"], rec["prato"]
                print(
                    f"   - {lugar['nome']} ({lugar['cozinha']}, {lugar['distancia_km']} km)"
                    f"{' · peça: ' + prato['nome'] if prato else ''} · {rec['motivo']}"
                )
            if a.trace:
                for passo in r.passos:
                    print("   ·", json.dumps(passo, ensure_ascii=False)[:300])
            print()
    except (KeyboardInterrupt, EOFError):
        print()


def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("bocado.api:app", host=a.host, port=a.port, reload=a.reload)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="bocado")
    sub = p.add_subparsers(required=True)

    i = sub.add_parser("init-db", help="aplica o esquema (com --reset, apaga tudo antes)")
    i.add_argument("--reset", action="store_true")
    i.set_defaults(f=cmd_init_db)

    sub.add_parser("aquecer", help="carrega os recortes salvos do OSM no banco (sem internet)").set_defaults(
        f=cmd_aquecer
    )

    sub.add_parser(
        "reclassificar", help="refaz as categorias dos lugares a partir das tags guardadas (após mudar as regras)"
    ).set_defaults(f=cmd_reclassificar)

    lo = sub.add_parser("local", help="resolve um CEP e baixa os lugares em volta")
    lo.add_argument("cep")
    lo.set_defaults(f=cmd_local)

    b = sub.add_parser("buscar", help="testa extração + busca de lugares reais perto de um CEP")
    b.add_argument("texto")
    b.add_argument("--cep", default="01310100")
    b.add_argument("--extrator", default=None)
    b.add_argument("--limite", type=int, default=5)
    b.set_defaults(f=cmd_buscar)

    c = sub.add_parser("chat", help="conversa com o agente no terminal")
    c.add_argument("--cep", default="01310100")
    c.add_argument("--trace", action="store_true")
    c.set_defaults(f=cmd_chat)

    s = sub.add_parser("serve", help="sobe a API e a demo web")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(f=cmd_serve)

    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
