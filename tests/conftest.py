"""Fixtures: Postgres de teste com recortes reais do OSM carregados e embeddings de hashing.

Usa TEST_DATABASE_URL se existir (no CI é um serviço pgvector); senão sobe um Postgres embutido
com o pacote pgserver em .cache/pgtest. Nada aqui acessa a internet: CEP e Overpass vêm de
recortes congelados em data/recortes.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from bocado.config import get_settings
from bocado.db import criar_pool, resetar
from bocado.embeddings import HashEmbedder, set_embedder
from bocado.extraction import ExtratorRegras
from bocado.local import osm
from bocado.local.cep import Local

RAIZ = Path(__file__).resolve().parents[1]
RECORTES = RAIZ / "data" / "recortes"


def _url_teste() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    pasta = RAIZ / ".cache" / "pgtest"
    pasta.mkdir(parents=True, exist_ok=True)
    return pgserver.get_server(str(pasta), cleanup_mode=None).get_uri()


def local_do_recorte(nome: str) -> Local:
    m = json.loads((RECORTES / f"{nome}.json").read_text(encoding="utf-8"))["meta"]
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


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _url_teste()
    os.environ["DATABASE_URL"] = url
    os.environ["LLM_PROVIDER"] = "none"
    os.environ["EXTRACTOR"] = "rules"
    get_settings.cache_clear()
    return url


@pytest.fixture(scope="session")
def embedder() -> HashEmbedder:
    e = HashEmbedder()
    set_embedder(e)
    return e


@pytest.fixture(scope="session")
def pool(db_url, embedder):
    mp = pytest.MonkeyPatch()
    mp.setattr(osm, "disparar_embeddings", lambda pool, embedder: None)  # nos testes, embeddings síncronos
    p = criar_pool(db_url, max_size=4)
    with p.connection() as conn:
        resetar(conn)
        for nome in ("sao-paulo-paulista", "lavras-mg"):
            loc = local_do_recorte(nome)
            conn.execute(
                "insert into ceps (cep, logradouro, bairro, cidade, uf, ibge, lat, lon, precisao) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (loc.cep, loc.logradouro, loc.bairro, loc.cidade, loc.uf, loc.ibge, loc.lat, loc.lon, loc.precisao),
            )
    tmp = RAIZ / ".cache" / "recortes-teste"
    tmp.mkdir(parents=True, exist_ok=True)
    for nome in ("sao-paulo-paulista", "lavras-mg"):
        (tmp / f"{nome}.json").write_text((RECORTES / f"{nome}.json").read_text(encoding="utf-8"), encoding="utf-8")
    osm.aquecer(p, embedder, [tmp])
    osm.calcular_embeddings(p, embedder)
    yield p
    p.close()
    mp.undo()


@pytest.fixture()
def conn(pool):
    with pool.connection() as c:
        yield c


@pytest.fixture()
def sp() -> Local:
    return local_do_recorte("sao-paulo-paulista")


@pytest.fixture()
def lavras() -> Local:
    return local_do_recorte("lavras-mg")


@pytest.fixture()
def visitante(pool, sp) -> str:
    vid = str(uuid.uuid4())
    with pool.connection() as c:
        c.execute("insert into visitantes (id, cep) values (%s, %s)", (vid, sp.cep))
    return vid


@pytest.fixture()
def extrator() -> ExtratorRegras:
    return ExtratorRegras()
