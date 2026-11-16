"""Testes sem banco: horário, classificação de cozinha, CEP, pratos típicos e regionais."""

from datetime import datetime

import pytest

from bocado.datagen.library import POOLS
from bocado.extraction.schema import CATEGORIAS
from bocado.local.categorias import classificar
from bocado.local.cep import CepInvalido, normalizar_cep
from bocado.local.horario import aberto_agora, horario_de_hoje
from bocado.local.osm import normalizar_elemento
from bocado.local.pratos import MINIMO_COSTUMA_TER, REGIONAIS, TIPICOS_POR_CATEGORIA, tipicos

SEG_20H = datetime(2026, 11, 16, 20, 0)  # segunda-feira
SEG_01H = datetime(2026, 11, 16, 1, 0)
DOM_13H = datetime(2026, 11, 15, 13, 0)


@pytest.mark.parametrize(
    "texto,quando,esperado",
    [
        ("24/7", SEG_01H, True),
        ("Mo-Fr 11:00-15:00,18:00-23:00", SEG_20H, True),
        ("Mo-Fr 11:00-15:00,18:00-23:00", DOM_13H, False),
        ("Mo-Su 11:00-23:00; Su off", DOM_13H, False),  # regra posterior sobrescreve
        ("Su 18:00-02:00", SEG_01H, True),  # faixa de domingo que vira a meia-noite
        ("Tu-Su 12:00-22:00", SEG_20H, False),
        ("sunrise-sunset", SEG_20H, None),  # não entendido: não chuta
        ("", SEG_20H, None),
        (None, SEG_20H, None),
    ],
)
def test_aberto_agora(texto, quando, esperado):
    assert aberto_agora(texto, quando) is esperado


def test_horario_de_hoje():
    assert horario_de_hoje("Mo-Fr 11:00-15:00,18:00-23:00", SEG_20H) == "11:00–15:00, 18:00–23:00"
    assert horario_de_hoje("Mo-Sa 08:00-18:00; Su off", DOM_13H) == "fechado hoje"
    assert horario_de_hoje("24/7", DOM_13H) == "24 horas"


@pytest.mark.parametrize(
    "tags,cats,origem",
    [
        ({"name": "X", "cuisine": "sushi;japanese"}, ["japonesa"], "osm"),
        ({"name": "X", "cuisine": "burger;pizza"}, ["hamburguer", "pizza"], "osm"),
        ({"name": "Pizzaria Bella"}, ["pizza"], "nome"),
        ({"name": "Temakeria do Japa"}, ["japonesa"], "nome"),
        ({"name": "Padaria Real"}, ["cafe"], "nome"),
        # o que a casa vende vence a origem; a origem vence "restaurante"
        ({"name": "Pastel do Japonês"}, ["lanches"], "nome"),
        ({"name": "Restaurante Japonês Sakura"}, ["japonesa"], "nome"),
        ({"name": "Esfiharia Árabe"}, ["arabe"], "nome"),
        ({"name": "Sorvetes Gelados", "amenity": "ice_cream"}, ["doces"], "tipo"),
        ({"name": "Redentor", "amenity": "restaurant"}, [], "nenhuma"),
    ],
)
def test_classificar(tags, cats, origem):
    c, _, o = classificar(tags)
    assert c == cats and o == origem


def test_cozinha_sem_categoria_mantem_rotulo():
    c, rotulos, origem = classificar({"name": "Tasca", "cuisine": "portuguese"})
    assert c == [] and rotulos == ["portuguesa"] and origem == "nenhuma"


def test_normalizar_elemento_le_dieta_e_ignora_sem_nome():
    e = {
        "type": "node",
        "id": 1,
        "lat": -23.5,
        "lon": -46.6,
        "tags": {"name": "Verde", "amenity": "restaurant", "diet:vegan": "only", "opening_hours": "24/7"},
    }
    lugar = normalizar_elemento(e)
    assert lugar["id"] == "osm:n1" and lugar["dieta"] == ["vegano"] and lugar["horario"] == "24/7"
    assert normalizar_elemento({"type": "node", "id": 2, "lat": 0, "lon": 0, "tags": {"amenity": "cafe"}}) is None


@pytest.mark.parametrize("cep", ["01310-100", "01310100", " 01310100 "])
def test_normalizar_cep(cep):
    assert normalizar_cep(cep) == "01310100"


@pytest.mark.parametrize("cep", ["123", "0131010a", "00000000", ""])
def test_cep_invalido(cep):
    with pytest.raises(CepInvalido):
        normalizar_cep(cep)


def test_regionais_cobrem_os_27_estados():
    assert len(REGIONAIS) == 27 and all(REGIONAIS[uf] for uf in REGIONAIS)
    for pratos in REGIONAIS.values():
        for p in pratos:
            assert set(p.onde) <= set(CATEGORIAS), p.nome


def test_restaurante_brasileiro_comum_nao_costuma_ter_vegano():
    veganos = sum(1 for p in TIPICOS_POR_CATEGORIA["brasileira"] if "vegano" in p.tags)
    assert veganos < MINIMO_COSTUMA_TER


def test_casa_vegana_confirmada_so_sugere_veganos():
    pratos = tipicos(["brasileira"], [], ["vegano"])
    assert pratos and all("vegano" in p.tags for p in pratos)


def test_casa_vegetariana_nao_sugere_salmao():
    # Bug real achado pela avaliação: restaurante "Vegetariano" em Recife com "Salada com salmão grelhado".
    pratos = tipicos(["saladas"], [], ["vegetariano"])
    assert pratos and all("vegetariano" in p.tags for p in pratos)


TAGS_VALIDAS = {
    "vegetariano",
    "vegano",
    "sem_gluten",
    "sem_lactose",
    "frito",
    "carne_vermelha",
    "porco",
    "frutos_do_mar",
    "apimentado",
    "leve",
    "proteico",
}


def test_tags_da_biblioteca_seguem_as_regras():
    for pool, (_, pratos) in POOLS.items():
        for nome, _, _preco, kcal, tags, _serve in pratos:
            t = set(tags.split())
            assert t <= TAGS_VALIDAS, (pool, nome)
            if "vegano" in t:
                assert {"vegetariano", "sem_lactose"} <= t, nome
            if "vegetariano" in t:
                assert not t & {"carne_vermelha", "porco", "frutos_do_mar"}, nome
            if "leve" in t:
                assert "frito" not in t and kcal <= 650, nome


def test_converte_resultado_do_nominatim_no_formato_do_overpass():
    from bocado.local.osm import _de_nominatim

    item = {
        "osm_type": "node",
        "osm_id": 42,
        "lat": "-23.5",
        "lon": "-46.6",
        "category": "amenity",
        "type": "restaurant",
        "name": "Cantina X",
        "extratags": {"cuisine": "italian", "opening_hours": "24/7"},
        "address": {"road": "Rua A", "house_number": "10"},
    }
    lugar = normalizar_elemento(_de_nominatim(item))
    assert lugar["id"] == "osm:n42" and lugar["categorias"] == ["italiana"] and lugar["endereco"] == "Rua A, 10"
    assert _de_nominatim({**item, "name": ""}) is None
