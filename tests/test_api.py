"""API de ponta a ponta, com CEP e Overpass substituídos por recortes (sem rede)."""

import pytest
from fastapi.testclient import TestClient

from bocado.api import LimitadorPorIP
from bocado.llm import set_llm
from bocado.local import cep as cep_mod
from bocado.local import osm


@pytest.fixture()
def cliente(pool, sp, monkeypatch):
    set_llm(None)

    def resolver(cep, conn=None):
        digitos = cep_mod.normalizar_cep(cep)
        if digitos == "99999999":
            raise cep_mod.CepNaoEncontrado("não existe")
        if digitos == "88888888":
            raise cep_mod.ServicoIndisponivel("fora do ar")
        return sp

    monkeypatch.setattr(cep_mod, "resolver", resolver)
    monkeypatch.setattr(osm, "consultar_overpass", lambda *a, **k: [])
    from bocado.api import app

    with TestClient(app) as c:
        yield c


def test_fluxo_completo(cliente):
    v = cliente.post("/api/local", json={"nome": "  Ana   Maria ", "cep": "01310-100"}).json()
    vid = v["visitante_id"]
    assert v["nome"] == "Ana Maria" and v["local"]["cidade"] == "São Paulo"
    assert v["area"]["estado"] == "pronta" and v["area"]["n_lugares"] > 500 and v["regionais"]
    assert cliente.get(f"/api/visitante/{vid}").json()["nome"] == "Ana Maria"

    r = cliente.post("/api/chat", json={"visitante_id": vid, "mensagem": "quero algo vegano"}).json()
    assert r["modo"] == "simples" and r["pratos"] and r["recomendacoes"] == []
    assert all("vegano" in p["tags"] for p in r["pratos"])

    prato = r["pratos"][0]["id"]
    onde = cliente.get("/api/onde-comer", params={"visitante_id": vid, "prato_id": prato})
    assert onde.status_code == 200 and onde.json()["recomendacoes"]
    assert cliente.get("/api/onde-comer", params={"visitante_id": vid, "prato_id": "nao:existe"}).status_code == 404
    assert cliente.get(f"/api/traces/{r['trace_id']}").json()["modo"] == "simples"

    assert cliente.delete(f"/api/visitante/{vid}").json()["ok"]
    assert cliente.get(f"/api/visitante/{vid}").status_code == 404
    assert cliente.post("/api/chat", json={"visitante_id": vid, "mensagem": "oi"}).status_code == 404


def test_onde_comer_espera_o_mapa(cliente, monkeypatch):
    vid = cliente.post("/api/local", json={"nome": "Bia", "cep": "01310100"}).json()["visitante_id"]
    monkeypatch.setattr(osm, "iniciar_area", lambda *a, **k: {"estado": "carregando", "desde_s": 3})
    r = cliente.get("/api/onde-comer", params={"visitante_id": vid, "prato_id": "pizza:pizza-margherita"})
    assert r.status_code == 202 and r.json()["area"]["estado"] == "carregando"


def test_erros_de_cep(cliente):
    assert cliente.post("/api/local", json={"cep": "1234"}).status_code == 422
    assert cliente.post("/api/local", json={"cep": "12345-67a"}).status_code == 422
    assert cliente.post("/api/local", json={"cep": "99999-999"}).status_code == 404
    assert cliente.post("/api/local", json={"cep": "88888-888"}).status_code == 503


def test_validacao(cliente):
    assert cliente.post("/api/chat", json={"visitante_id": "x", "mensagem": "oi"}).status_code == 422
    assert cliente.get("/api/traces/nao-existe").status_code == 404


def test_saude_e_pagina(cliente):
    assert cliente.get("/api/saude").json()["lugares_em_cache"] > 500
    r = cliente.get("/")
    assert r.status_code == 200 and "bora de quê?" in r.text


def test_limitador():
    lim = LimitadorPorIP(2, 60)
    assert lim.permitir("a") and lim.permitir("a") and not lim.permitir("a") and lim.permitir("b")
