"""Busca sobre lugares reais (recortes congelados de São Paulo e Lavras)."""

import random
from datetime import datetime

from bocado.extraction.schema import CATEGORIAS, EVITAR, RESTRICOES, Filtros
from bocado.local.busca import TAG_EVITAR, Origem, buscar_lugares, combinacao_ok, status_dieta
from bocado.local.pratos import tipicos

AGORA = datetime(2026, 11, 16, 20, 0)


def _origem(local):
    return Origem(local.lat, local.lon, local.uf, local.fuso)


def test_restricoes_e_evitar_nunca_sao_violados(conn, embedder, sp):
    rng = random.Random(0)
    for _ in range(40):
        f = Filtros(
            categorias=rng.sample(CATEGORIAS, k=rng.choice([0, 0, 1])),
            restricoes=rng.sample(RESTRICOES, k=rng.choice([0, 1, 1])),
            evitar=rng.sample(EVITAR, k=rng.choice([0, 1])),
            distancia_max_km=rng.choice([None, 1, 2]),
        )
        r = buscar_lugares(conn, embedder, f, "algo gostoso", _origem(sp), limite=5, agora=AGORA)
        for lugar in r.lugares:
            assert lugar["distancia_km"] <= r.raio_km + 1e-6
            if f.categorias:
                assert set(lugar["categorias"]) & set(f.categorias)
            for restricao in f.restricoes:
                assert lugar["dieta_status"][restricao] in ("confirmado", "costuma ter")
            for s in lugar["sugestoes"]:
                assert set(f.restricoes) <= set(s["tags"]) or s["origem"] == "regional"
                assert not any(TAG_EVITAR[e] in s["tags"] for e in f.evitar)


def test_vegano_traz_lugares_confirmados_no_topo(conn, embedder, sp):
    r = buscar_lugares(conn, embedder, Filtros(restricoes=["vegano"]), "comida vegana", _origem(sp), agora=AGORA)
    assert r.lugares and r.lugares[0]["dieta_status"]["vegano"] == "confirmado"


def test_sem_nomes_repetidos(conn, embedder, sp):
    r = buscar_lugares(conn, embedder, Filtros(categorias=["cafe"]), "café", _origem(sp), limite=6, agora=AGORA)
    nomes = [x["nome"].lower() for x in r.lugares]
    assert len(nomes) == len(set(nomes))


def test_sugestoes_priorizam_a_cozinha_pedida(conn, embedder, sp):
    r = buscar_lugares(conn, embedder, Filtros(categorias=["japonesa"]), "japa", _origem(sp), agora=AGORA)
    for lugar in r.lugares:
        assert lugar["sugestoes"], lugar["nome"]


def test_pratos_regionais_aparecem(conn, embedder, sp):
    r = buscar_lugares(conn, embedder, Filtros(categorias=["pizza"]), "pizza", _origem(sp), agora=AGORA)
    assert any(s["origem"] == "regional" for x in r.lugares for s in x["sugestoes"])  # pizza paulistana


def test_avisa_o_que_nao_existe_nos_dados(conn, embedder, sp):
    f = Filtros(preco_max=40, ordenar_por="avaliacao", tempo_max_min=30)
    r = buscar_lugares(conn, embedder, f, "barato", _origem(sp), agora=AGORA)
    texto = " ".join(r.avisos)
    assert "preços" in texto and "notas" in texto and "tempo de entrega" in texto


def test_so_abertos_exclui_fechados(conn, embedder, sp):
    r = buscar_lugares(conn, embedder, Filtros(), "comer", _origem(sp), limite=6, so_abertos=True, agora=AGORA)
    assert all(x["aberto_agora"] is not False for x in r.lugares)


def test_cidade_com_poucos_lugares_relaxa_distancia(conn, embedder, lavras):
    r = buscar_lugares(conn, embedder, Filtros(distancia_max_km=0.3), "comer", _origem(lavras), agora=AGORA)
    assert r.lugares and "distancia" in r.relaxados


def test_onde_comer_por_cozinha(conn, embedder, sp):
    from bocado.local.busca import onde_comer
    from bocado.local.cardapio import POR_ID

    r = onde_comer(conn, embedder, POR_ID["japonesa:sashimi-de-salmao"], _origem(sp), [], [], agora=AGORA)
    assert r.lugares and all("japonesa" in x["categorias"] for x in r.lugares)
    assert all(x["evidencia"].startswith("costuma ter") or "nome" in x["evidencia"] for x in r.lugares)


def test_onde_comer_tenta_cozinha_relacionada(conn, embedder, lavras):
    from bocado.local.busca import onde_comer
    from bocado.local.cardapio import POR_ID

    # Lavras não tem casa de poke mapeada: cai para cozinhas relacionadas e avisa que é "pode ter".
    r = onde_comer(conn, embedder, POR_ID["poke:poke-de-salmao"], _origem(lavras), [], [], agora=AGORA)
    if r.lugares:
        assert "cozinha" in r.relaxados and all(x["evidencia"].startswith("pode ter") for x in r.lugares)


def test_leva_rapida_deixa_a_area_parcial_e_depois_pronta(pool, embedder, monkeypatch):
    import time

    from bocado.local import osm

    lat, lon = -3.7319, -38.5267  # longe dos recortes dos testes
    lote = [
        {
            "type": "node",
            "id": 9100000001,
            "lat": lat,
            "lon": lon,
            "tags": {"name": "Pizzaria Rápida", "amenity": "restaurant", "cuisine": "pizza"},
        }
    ]
    monkeypatch.setattr(osm, "consultar_rapido", lambda *a, **k: iter([lote]))

    def overpass_lento(*a, **k):
        time.sleep(0.5)
        return lote

    monkeypatch.setattr(osm, "consultar_overpass", overpass_lento)
    assert osm.iniciar_area(pool, embedder, lat, lon)["estado"] == "carregando"
    vistos = set()
    for _ in range(200):
        estado = osm.estado_area(pool, lat, lon)["estado"]
        vistos.add(estado)
        if estado == "pronta":
            break
        time.sleep(0.05)
    assert "parcial" in vistos and estado == "pronta"


def test_restricoes_deduzidas_precisam_valer_juntas():
    # Café tem pratos sem glúten e pratos sem lactose, mas nenhum típico com as duas coisas.
    restricoes = ("sem_gluten", "sem_lactose")
    cafe = tipicos(["cafe"], [], [])
    dieta = {r: status_dieta(r, [], cafe) for r in restricoes}
    assert all(v == "costuma ter" for v in dieta.values()) and not combinacao_ok(dieta, cafe)
    # O que o OpenStreetMap confirma não depende dos pratos típicos.
    assert combinacao_ok({"sem_gluten": "confirmado", "sem_lactose": "costuma ter"}, cafe)


def test_busca_com_duas_restricoes_sempre_tem_prato_que_atende_as_duas(conn, embedder, sp):
    f = Filtros(restricoes=["sem_gluten", "sem_lactose"])
    r = buscar_lugares(conn, embedder, f, "jantar sem glúten e sem lactose", _origem(sp), limite=8, agora=AGORA)
    assert r.lugares
    for lugar in r.lugares:
        assert lugar["sugestoes"] and {"sem_gluten", "sem_lactose"} <= set(lugar["sugestoes"][0]["tags"]), lugar["nome"]
