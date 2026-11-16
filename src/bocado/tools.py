"""Ferramentas do agente.

Fluxo do app: primeiro O QUE comer (pratos), depois ONDE comer (lugares reais perto).
Cada ferramenta valida os argumentos com pydantic e devolve um dicionário enxuto. Pratos devolvidos
entram em `ctx.pratos_vistos` e lugares em `ctx.vistos`: só eles podem ser recomendados.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field, ValidationError

from bocado.embeddings import Embedder
from bocado.extraction import Extrator, rules
from bocado.extraction.schema import EVITAR, RESTRICOES, Filtros
from bocado.llm.base import Ferramenta
from bocado.local import cardapio
from bocado.local.busca import Origem, avisos_sem_dados, buscar_lugares, onde_comer
from bocado.local.cep import Local


@dataclass
class Contexto:
    conn: psycopg.Connection
    embedder: Embedder
    extrator: Extrator
    visitante_id: str
    local: Local
    vistos: dict[str, dict[str, Any]] = field(default_factory=dict)
    pratos_vistos: dict[str, dict[str, Any]] = field(default_factory=dict)
    mensagem: str = ""  # texto original do usuário neste turno
    area_pronta: bool = True  # os lugares da região já foram baixados?
    _duras_mensagem: tuple[list[str], list[str]] | None = None

    @property
    def origem(self) -> Origem:
        return Origem(lat=self.local.lat, lon=self.local.lon, uf=self.local.uf, fuso=self.local.fuso)

    def duras_da_mensagem(self) -> tuple[list[str], list[str]]:
        """Restrições e itens a evitar ditos pelo usuário nesta mensagem, lidos do texto original.

        O LLM reescreve o pedido antes de buscar e às vezes perde uma restrição no caminho
        ("nada apimentado" sumia). Estas são aplicadas sempre. Usa o extrator de regras de
        propósito: é instantâneo e falha de um jeito diferente do modelo (defesa em camadas).
        """
        if self._duras_mensagem is None:
            f = rules.extrair(self.mensagem) if self.mensagem else Filtros()
            self._duras_mensagem = (list(f.restricoes), list(f.evitar))
        return self._duras_mensagem


def preferencias(conn: psycopg.Connection, visitante_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"restricao": [], "evitar": [], "gosto": []}
    for p in conn.execute(
        "select tipo, valor from preferencias where visitante_id = %s order by criado_em", (visitante_id,)
    ).fetchall():
        out[p["tipo"]].append(p["valor"])
    return out


def _cartao_lugar(lugar: dict[str, Any]) -> dict[str, Any]:
    """O que volta para o LLM sobre um lugar: o suficiente para escolher e justificar."""
    d = {
        "id": lugar["id"],
        "nome": lugar["nome"],
        "cozinha": lugar.get("cozinha") or "não informada",
        "distancia_km": lugar["distancia_km"],
    }
    if lugar.get("evidencia"):
        d["por_que_serve"] = lugar["evidencia"]
    elif lugar.get("sugestoes"):
        d["pratos_tipicos_sugeridos"] = [s["nome"] for s in lugar["sugestoes"]]
    if lugar.get("dieta_status"):
        d["dieta"] = lugar["dieta_status"]
    if lugar.get("aberto_agora") is not None:
        d["aberto_agora"] = lugar["aberto_agora"]
    if lugar.get("horario_hoje"):
        d["horario_hoje"] = lugar["horario_hoje"]
    return d


def _filtros_do_pedido(ctx: Contexto, pedido: str) -> tuple[Filtros, Any, dict, tuple[list[str], list[str]]]:
    res_ext = ctx.extrator.extrair(pedido)
    mem = preferencias(ctx.conn, ctx.visitante_id)
    rest_msg, evitar_msg = ctx.duras_da_mensagem()
    filtros = res_ext.filtros.mesclar_memoria(mem["restricao"] + rest_msg, mem["evitar"] + evitar_msg)
    return filtros, res_ext, mem, (rest_msg, evitar_msg)


def _diagnostico(ctx: Contexto, res_ext, mem: dict, duras: tuple[list[str], list[str]]) -> dict[str, Any]:
    rest_msg, evitar_msg = duras
    out: dict[str, Any] = {
        "_extrator": {
            "nome": ctx.extrator.nome,
            "valido": res_ext.valido,
            "latencia_ms": round(res_ext.latencia_ms),
            "erro": res_ext.erro,
        }
    }
    reforco = sorted(
        (set(rest_msg) - set(res_ext.filtros.restricoes) - set(mem["restricao"]))
        | (set(evitar_msg) - set(res_ext.filtros.evitar) - set(mem["evitar"]))
    )
    if reforco:
        out["_reforco_da_mensagem"] = reforco
    return out


# ---------------------------------------------------------------- argumentos


class ArgsPratos(BaseModel):
    pedido: str = Field(min_length=1, max_length=400)
    limite: int = Field(default=4, ge=1, le=6)


class ArgsOndeComer(BaseModel):
    prato_id: str = Field(min_length=3, max_length=120)
    limite: int = Field(default=4, ge=1, le=6)


class ArgsBuscar(BaseModel):
    pedido: str = Field(min_length=1, max_length=400)
    limite: int = Field(default=4, ge=1, le=6)
    so_abertos: bool = False


class ArgsPreferencia(BaseModel):
    tipo: Literal["restricao", "evitar", "gosto"]
    valor: str = Field(min_length=2, max_length=60)


class PratoEscolhido(BaseModel):
    prato_id: str
    motivo: str = Field(default="", max_length=220)


class LugarEscolhido(BaseModel):
    lugar_id: str
    prato_id: str | None = Field(default=None, max_length=120)
    motivo: str = Field(default="", max_length=220)


class ArgsResponder(BaseModel):
    mensagem: str = Field(min_length=1, max_length=1200)
    pratos: list[PratoEscolhido] = Field(default_factory=list, max_length=6)
    lugares: list[LugarEscolhido] = Field(default_factory=list, max_length=5)


# ---------------------------------------------------------------- execução


def sugerir_pratos(ctx: Contexto, a: ArgsPratos) -> dict[str, Any]:
    filtros, res_ext, mem, duras = _filtros_do_pedido(ctx, a.pedido)
    pratos = cardapio.sugerir(ctx.embedder, filtros, a.pedido, ctx.local.uf, limite=a.limite)
    for p in pratos:
        ctx.pratos_vistos[p["id"]] = p
    out: dict[str, Any] = {
        "filtros_aplicados": filtros.compacto(),
        "pratos": [
            {
                "id": p["id"],
                "nome": p["nome"],
                "cozinha": p["cozinha"],
                "descricao": p["descricao"],
                "tags": p["tags"],
                **({"regional_de": p["regional"]} if p["regional"] else {}),
            }
            for p in pratos
        ],
    }
    if not pratos:
        out["aviso"] = "Nenhum prato do catálogo atende a tudo isso. Diga isso e sugira afrouxar algo."
    if avisos := avisos_sem_dados(filtros):
        out["avisos"] = avisos
    return out | _diagnostico(ctx, res_ext, mem, duras)


def _onde(ctx: Contexto, prato_id: str, limite: int) -> dict[str, Any]:
    if not ctx.area_pronta:
        return {
            "aviso": "Ainda estou baixando os restaurantes da região do usuário (leva até 1 minuto na primeira "
            "vez). Sugira pratos agora e diga para tocar em 'Onde comer perto' daqui a pouco."
        }
    prato = cardapio.POR_ID.get(prato_id)
    if prato is None:
        return {"erro": f"prato_id desconhecido: {prato_id}. Use um id devolvido por sugerir_pratos."}
    mem = preferencias(ctx.conn, ctx.visitante_id)
    rest_msg, evitar_msg = ctx.duras_da_mensagem()
    r = onde_comer(
        ctx.conn,
        ctx.embedder,
        prato,
        ctx.origem,
        mem["restricao"] + rest_msg,
        mem["evitar"] + evitar_msg,
        limite=limite,
    )
    for lugar in r.lugares:
        lugar["prato"] = prato.publico()
        ctx.vistos[lugar["id"]] = lugar
    ctx.pratos_vistos[prato.id] = prato.publico()
    out: dict[str, Any] = {"prato": prato.nome, "raio_km": r.raio_km, "lugares": [_cartao_lugar(x) for x in r.lugares]}
    if r.relaxados:
        out["filtros_relaxados"] = r.relaxados
    if not r.lugares:
        out["aviso"] = "Nenhum lugar mapeado por perto costuma servir isso. Diga isso e sugira outro prato."
    return out


def onde_comer_tool(ctx: Contexto, a: ArgsOndeComer) -> dict[str, Any]:
    return _onde(ctx, a.prato_id, a.limite)


def buscar(ctx: Contexto, a: ArgsBuscar) -> dict[str, Any]:
    if not ctx.area_pronta:
        return {
            "aviso": "Ainda estou baixando os restaurantes da região do usuário (leva até 1 minuto na primeira "
            "vez). Sugira pratos agora e diga para tocar em 'Onde comer perto' daqui a pouco."
        }
    filtros, res_ext, mem, duras = _filtros_do_pedido(ctx, a.pedido)
    r = buscar_lugares(ctx.conn, ctx.embedder, filtros, a.pedido, ctx.origem, limite=a.limite, so_abertos=a.so_abertos)
    for lugar in r.lugares:
        ctx.vistos[lugar["id"]] = lugar
    out: dict[str, Any] = {
        "filtros_aplicados": filtros.compacto(),
        "raio_km": r.raio_km,
        "lugares": [_cartao_lugar(x) for x in r.lugares],
    }
    if r.relaxados:
        out["filtros_relaxados"] = r.relaxados
    if r.avisos:
        out["avisos"] = r.avisos
    if not r.lugares:
        out["aviso"] = "Nenhum lugar mapeado por perto atende ao pedido. Diga isso e sugira afrouxar algo."
    return out | _diagnostico(ctx, res_ext, mem, duras)


def salvar_preferencia(ctx: Contexto, a: ArgsPreferencia) -> dict[str, Any]:
    valor = a.valor.strip().lower().replace(" ", "_") if a.tipo != "gosto" else a.valor.strip()
    if a.tipo == "restricao" and valor not in RESTRICOES:
        return {"erro": f"restricao deve ser uma de {list(RESTRICOES)}"}
    if a.tipo == "evitar" and valor not in EVITAR:
        return {"erro": f"evitar deve ser um de {list(EVITAR)}; para outras coisas use tipo 'gosto'"}
    ctx.conn.execute(
        "insert into preferencias (visitante_id, tipo, valor) values (%s, %s, %s) on conflict do nothing",
        (ctx.visitante_id, a.tipo, valor),
    )
    return {"ok": True, "salvo": {"tipo": a.tipo, "valor": valor}}


# ---------------------------------------------------------------- catálogo para o LLM


def _schema(modelo: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema enxuto, sem $defs nem anyOf (o Gemini prefere esquemas planos)."""
    s = modelo.model_json_schema()
    defs = s.pop("$defs", {})

    def resolver(n: Any) -> Any:
        if isinstance(n, dict):
            if "$ref" in n:
                return resolver(defs[n["$ref"].split("/")[-1]])
            if "anyOf" in n:  # campos opcionais (str | None) viram só o tipo não nulo
                outros = [x for x in n["anyOf"] if x.get("type") != "null"]
                base = {k: v for k, v in n.items() if k != "anyOf"}
                return resolver({**base, **outros[0]})
            return {k: resolver(v) for k, v in n.items() if k not in ("title", "default")}
        if isinstance(n, list):
            return [resolver(x) for x in n]
        return n

    return resolver(s)


FERRAMENTAS: dict[str, tuple[type[BaseModel], Any, str]] = {
    "sugerir_pratos": (
        ArgsPratos,
        sugerir_pratos,
        "Sugere PRATOS (o que comer) a partir do pedido: entende cozinha, restrições, o que evitar e vontades "
        "(leve, proteico, apimentado), e dá destaque aos pratos típicos do estado do usuário. Use por padrão. "
        "Passe o pedido como frase completa, com o contexto da conversa que ainda vale.",
    ),
    "onde_comer": (
        ArgsOndeComer,
        onde_comer_tool,
        "Lugares REAIS perto do usuário (OpenStreetMap) onde um prato costuma existir, com distância e horário. "
        "Use quando o usuário quiser saber onde comer um prato já sugerido (passe o prato_id).",
    ),
    "buscar_lugares": (
        ArgsBuscar,
        buscar,
        "Busca lugares REAIS perto do usuário direto por tipo de casa ('um japa perto', 'o que está aberto agora'). "
        "Use só quando o pedido for claramente por um lugar, não por um prato.",
    ),
    "salvar_preferencia": (
        ArgsPreferencia,
        salvar_preferencia,
        "Guarda uma preferência duradoura. tipo=restricao (vegetariano, vegano, sem_gluten, sem_lactose), "
        "tipo=evitar (fritura, carne_vermelha, porco, frutos_do_mar, pimenta) ou tipo=gosto (texto curto).",
    ),
    "responder": (
        ArgsResponder,
        None,
        "Encerra o turno. pratos: até 4 prato_id devolvidos pelas ferramentas, com motivo curto. lugares: até 3 "
        "lugar_id devolvidos pelas ferramentas (só quando o usuário pediu lugar), com motivo curto.",
    ),
}


def especificacoes() -> list[Ferramenta]:
    return [Ferramenta(nome=n, descricao=d, parametros=_schema(m)) for n, (m, _, d) in FERRAMENTAS.items()]


def executar(ctx: Contexto, nome: str, args: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Roda uma ferramenta. Erros viram {'erro': ...} para o modelo poder se corrigir."""
    inicio = time.perf_counter()
    if nome not in FERRAMENTAS or FERRAMENTAS[nome][1] is None:
        return {"erro": f"ferramenta desconhecida: {nome}"}, 0
    modelo, funcao, _ = FERRAMENTAS[nome]
    try:
        resultado = funcao(ctx, modelo.model_validate(args))
    except ValidationError as e:
        resultado = {"erro": f"argumentos inválidos: {e.errors()[0]['loc']} {e.errors()[0]['msg']}"}
    return resultado, int((time.perf_counter() - inicio) * 1000)
