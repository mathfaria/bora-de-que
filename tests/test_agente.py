"""Agente com LLM roteirizado (sem rede): pratos primeiro, lugares reais quando pedem."""

import uuid

from bocado.agent import Agente, lugares_do_prato
from bocado.extraction import ExtratorRegras
from bocado.llm.base import LLMIndisponivel, LLMRespostaInvalida
from bocado.llm.fake import LLMFalso, chamada


class Roteiro(LLMFalso):
    """LLM falso que decide o próximo turno olhando o que recebeu (para usar IDs reais)."""

    def __init__(self, passos):
        super().__init__([])
        self.passos = list(passos)

    def proximo(self, entrada):
        self.recebido.append(entrada)
        passo = self.passos.pop(0)
        return passo(self) if callable(passo) else passo


def _ultimo(llm, chave: str) -> dict:
    for entrada in reversed(llm.recebido):
        if isinstance(entrada, list):
            for _, res in entrada:
                if res.get(chave):
                    return res[chave][0]
    raise AssertionError(f"nenhuma ferramenta devolveu {chave}")


def _agente(pool, embedder, llm, **kw):
    return Agente(pool, llm=llm, embedder=embedder, extrator=ExtratorRegras(), **kw)


def _cota(_):
    raise LLMIndisponivel("429")


def test_pratos_primeiro_sem_restaurantes(pool, embedder, sp, visitante):
    llm = Roteiro(
        [
            chamada("sugerir_pratos", pedido="algo leve"),
            lambda s: chamada(
                "responder", mensagem="Que tal?", pratos=[{"prato_id": _ultimo(s, "pratos")["id"], "motivo": "leve"}]
            ),
        ]
    )
    r = _agente(pool, embedder, llm).responder(visitante, sp, "quero algo leve", nome="Ana")
    assert r.modo == "agente" and len(r.pratos) == 1 and r.recomendacoes == []
    assert "leve" in r.pratos[0]["tags"]
    assert "Ana" in llm.sistema


def test_onde_comer_traz_lugares_reais_do_banco(pool, embedder, conn, sp, visitante):
    llm = Roteiro(
        [
            chamada("sugerir_pratos", pedido="comida japonesa"),
            lambda s: chamada("onde_comer", prato_id=_ultimo(s, "pratos")["id"]),
            lambda s: chamada("responder", mensagem="Aqui perto:", lugares=[{"lugar_id": _ultimo(s, "lugares")["id"]}]),
        ]
    )
    r = _agente(pool, embedder, llm).responder(visitante, sp, "onde como um japa?")
    assert len(r.recomendacoes) == 1
    lugar = r.recomendacoes[0]["lugar"]
    real = conn.execute("select nome from lugares where id = %s", (lugar["id"],)).fetchone()
    assert lugar["nome"] == real["nome"] and lugar["mapa_url"].startswith("https://www.google.com/maps")
    assert r.recomendacoes[0]["prato"]["categoria"] == "japonesa"


def test_id_inventado_pede_correcao(pool, embedder, sp, visitante):
    llm = Roteiro(
        [
            chamada("sugerir_pratos", pedido="pizza"),
            chamada(
                "responder",
                mensagem="a",
                pratos=[{"prato_id": "pizza:lagosta-dourada"}],
                lugares=[{"lugar_id": "osm:n999999999"}],
            ),
            lambda s: chamada("responder", mensagem="b", pratos=[{"prato_id": _ultimo(s, "pratos")["id"]}]),
        ]
    )
    r = _agente(pool, embedder, llm).responder(visitante, sp, "pizza")
    assert r.metricas["ids_invalidos_1a_tentativa"] == 2 and r.metricas["correcoes"] == 1
    assert len(r.pratos) == 1 and r.recomendacoes == []


def test_llm_fora_do_ar_sugere_pratos_no_modo_simples(pool, embedder, sp, visitante):
    for erro in (LLMIndisponivel("429"), LLMRespostaInvalida("vazio")):
        r = _agente(pool, embedder, LLMFalso([erro])).responder(visitante, sp, "quero algo vegano")
        assert r.modo == "simples" and r.aviso and r.pratos and r.recomendacoes == []
        assert all("vegano" in p["tags"] for p in r.pratos)


def test_llm_cai_depois_de_onde_comer_usa_os_lugares(pool, embedder, sp, visitante):
    llm = Roteiro([chamada("onde_comer", prato_id="arabe:esfiha-de-carne"), _cota])
    r = _agente(pool, embedder, llm).responder(visitante, sp, "onde como esfiha?")
    assert r.modo == "simples" and r.recomendacoes and any(p["tipo"] == "falha_llm" for p in r.passos)


def test_restricao_dita_pelo_usuario_vale_mesmo_se_o_llm_esquecer(pool, embedder, sp, visitante):
    llm = Roteiro([chamada("sugerir_pratos", pedido="comida tailandesa"), chamada("responder", mensagem="ok")])
    r = _agente(pool, embedder, llm).responder(visitante, sp, "tailandesa, mas sou vegano")
    passo = next(p for p in r.passos if p.get("nome") == "sugerir_pratos")
    assert passo["resultado"]["_reforco_da_mensagem"] == ["vegano"]
    assert "vegano" in passo["resultado"]["filtros_aplicados"]["restricoes"]


def test_preferencia_salva_vale_na_conversa_seguinte(pool, embedder, sp, visitante):
    llm = Roteiro(
        [
            chamada("salvar_preferencia", tipo="restricao", valor="vegetariano"),
            chamada("responder", mensagem="anotado"),
            chamada("sugerir_pratos", pedido="pizza"),
            chamada("responder", mensagem="ok"),
        ]
    )
    ag = _agente(pool, embedder, llm)
    ag.responder(visitante, sp, "sou vegetariano")
    r2 = ag.responder(visitante, sp, "quero pizza")  # conversa nova: a memória vale igual
    passo = next(p for p in r2.passos if p.get("nome") == "sugerir_pratos")
    assert "vegetariano" in passo["resultado"]["filtros_aplicados"]["restricoes"]


def test_area_ainda_baixando_avisa_o_modelo(pool, embedder, sp, visitante):
    llm = Roteiro([chamada("onde_comer", prato_id="pizza:pizza-margherita"), chamada("responder", mensagem="já já")])
    _agente(pool, embedder, llm).responder(visitante, sp, "onde como pizza?", area_pronta=False)
    assert "baixando" in llm.recebido[1][0][1]["aviso"]


def test_modo_simples_nao_busca_frase_que_depende_da_conversa(pool, embedder, sp, visitante):
    ag = Agente(pool, llm=None, embedder=embedder, extrator=ExtratorRegras())
    ag.llm = None
    r1 = ag.responder(visitante, sp, "quero hambúrguer")
    r2 = ag.responder(visitante, sp, "gostei do primeiro", conversa_id=r1.conversa_id)
    assert r1.pratos and r2.pratos == [] and "modo simplificado" in r2.mensagem


def test_conversa_de_outro_visitante_nao_e_reaproveitada(pool, embedder, sp, visitante):
    outro = str(uuid.uuid4())
    with pool.connection() as c:
        c.execute("insert into visitantes (id, cep) values (%s, %s)", (outro, sp.cep))
    ag = _agente(pool, embedder, LLMFalso([chamada("responder", mensagem="oi")]))
    r1 = ag.responder(visitante, sp, "oi")
    ag.llm = LLMFalso([chamada("responder", mensagem="oi")])
    r2 = ag.responder(outro, sp, "oi", conversa_id=r1.conversa_id)
    assert r2.conversa_id != r1.conversa_id


def test_lugares_do_prato_sem_llm(pool, embedder, sp, visitante):
    r = lugares_do_prato(pool, embedder, visitante, sp, "regional-sp:pizza-paulistana")
    assert r["recomendacoes"] and all("pizza" in x["lugar"]["categorias"] for x in r["recomendacoes"])


def test_pedido_com_preco_avisa_o_modelo(pool, embedder, sp, visitante):
    llm = Roteiro([chamada("sugerir_pratos", pedido="sushi até 60 reais"), chamada("responder", mensagem="ok")])
    _agente(pool, embedder, llm).responder(visitante, sp, "sushi até 60 reais")
    resultado = llm.recebido[1][0][1]
    assert any("preços" in a for a in resultado["avisos"])
