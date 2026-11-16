"""Catálogo de pratos e sugestão (o "o que comer"), sem banco."""

from bocado.extraction.schema import Filtros
from bocado.local.cardapio import CATALOGO, POR_ID, sugerir


def test_ids_unicos_e_estaveis():
    assert len(POR_ID) == len(CATALOGO)
    assert "japonesa:sashimi-de-salmao" in POR_ID
    assert "regional-mg:pao-de-queijo" in POR_ID


def test_sugestao_respeita_restricoes_e_evitar(embedder):
    f = Filtros(restricoes=["vegano"], evitar=["fritura"])
    pratos = sugerir(embedder, f, "algo gostoso", "SP", limite=6)
    assert pratos
    for p in pratos:
        assert "vegano" in p["tags"] and "frito" not in p["tags"] and not p["regional"]


def test_regionais_so_do_estado_do_usuario(embedder):
    pratos = sugerir(embedder, Filtros(), "comida típica daqui", "MG", limite=6)
    regionais = [p["regional"] for p in pratos if p["regional"]]
    assert regionais and set(regionais) == {"MG"}


def test_pedido_de_atributo_nao_traz_regional_sem_tag(embedder):
    # bug real: "algo leve" em Curitiba trouxe "Carne de onça" (regional, sem tags)
    pratos = sugerir(embedder, Filtros(atributos=["leve"]), "algo leve", "PR", limite=6)
    assert pratos and not any(p["regional"] for p in pratos)


def test_variedade_de_cozinhas(embedder):
    pratos = sugerir(embedder, Filtros(), "me surpreenda", "SP", limite=6)
    cozinhas = [p["categoria"] for p in pratos]
    assert max(cozinhas.count(c) for c in cozinhas) <= 2


def test_prato_de_casa_vegana_so_quando_pedem_vegano(embedder):
    sem = sugerir(embedder, Filtros(), "feijoada", "RJ", limite=6)
    assert not any(p["exige_dieta"] for p in sem)
    com = sugerir(embedder, Filtros(restricoes=["vegano"]), "feijoada", "RJ", limite=6)
    assert any(p["id"] == "brasileira:feijoada-vegana" for p in com)
