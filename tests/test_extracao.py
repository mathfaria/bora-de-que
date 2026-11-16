import pytest

from bocado.avaliacao import carregar_casos, metricas_extracao
from bocado.extraction import ExtratorComReserva, ExtratorLLM, rules
from bocado.extraction.schema import ErroDeParse, Filtros, json_schema_filtros, parse_filtros
from bocado.llm.fake import LLMFalso


def test_compacto_omite_padroes_e_mantem_ordem():
    f = Filtros(preco_max=40.0, categorias=["pizza", "arabe", "pizza"], ordenar_por="menor_preco")
    assert f.compacto() == {"categorias": ["arabe", "pizza"], "preco_max": 40, "ordenar_por": "menor_preco"}
    assert Filtros().compacto() == {}


@pytest.mark.parametrize(
    "bruto",
    [
        '{"categorias": ["japonesa"], "preco_max": 60}',
        '```json\n{"categorias": ["japonesa"], "preco_max": 60}\n```',
        'Claro! {"categorias": ["japonesa"], "preco_max": 60, "distancia_max_km": null}',
    ],
)
def test_parse_tolera_cercas_e_nulos(bruto):
    assert parse_filtros(bruto) == Filtros(categorias=["japonesa"], preco_max=60)


@pytest.mark.parametrize(
    "bruto",
    [
        '{"categorias": ["sorvete"]}',
        '{"preco_max": -3}',
        "sem json",
        '["lista"]',
        '{"categorias": [',
        '{"tipo": "comida", "preco": "$0.50"}',
    ],
)
def test_parse_rejeita_fora_do_esquema(bruto):
    with pytest.raises(ErroDeParse):
        parse_filtros(bruto)


def test_mesclar_memoria_ignora_valores_desconhecidos():
    f = Filtros(restricoes=["sem_lactose"]).mesclar_memoria(["vegano", "keto"], ["pimenta", "coentro"])
    assert f.restricoes == ["sem_lactose", "vegano"] and f.evitar == ["pimenta"]


def test_schema_json_cobre_todos_os_campos():
    s = json_schema_filtros()
    assert set(s["required"]) == set(Filtros.model_fields)


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("pizza até 50 reais", {"categorias": ["pizza"], "preco_max": 50}),
        (
            "algo vegano sem pimenta em 25 minutos",
            {"restricoes": ["vegano"], "evitar": ["pimenta"], "tempo_max_min": 25},
        ),
        ("sushi num raio de 4 km", {"categorias": ["japonesa"], "distancia_max_km": 4}),
        ("o mais barato possível", {"ordenar_por": "menor_preco"}),
    ],
)
def test_regras_casos_basicos(texto, esperado):
    assert rules.extrair(texto).compacto() == esperado


def test_extrator_llm_usa_reserva_quando_json_invalido():
    ext = ExtratorLLM(LLMFalso(json_saida="isso não é json"))
    r = ext.extrair("pizza até 50 reais")
    assert not r.valido and r.filtros.categorias == ["pizza"]


def test_extrator_com_reserva_nao_propaga_erro():
    class Quebrado:
        nome = "quebrado"

        def extrair(self, texto):
            raise RuntimeError("modelo fora do ar")

    r = ExtratorComReserva(Quebrado()).extrair("pizza")
    assert r.filtros.categorias == ["pizza"] and "reserva" in r.erro


def test_conjunto_de_referencia_e_valido():
    casos = carregar_casos()
    assert len(casos) == 100
    assert len({c["id"] for c in casos}) == 100
    m = metricas_extracao([(c["gold"], c["gold"]) for c in casos])
    assert m["acerto_exato"] == 1.0


def test_conjunto_desafio_e_valido():
    from bocado.avaliacao import caminho_casos

    casos = carregar_casos(caminho_casos("desafio"))
    assert len(casos) == 80 and len({c["id"] for c in casos}) == 80
