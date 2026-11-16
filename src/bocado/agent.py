"""O agente: laço de ferramentas, trava de grounding, memória, tracing e modo simples.

Fluxo: primeiro O QUE comer (pratos); lugares reais só quando a pessoa pede onde comer.
Garantias que valem em qualquer resposta:
- só aparecem pratos e lugares que alguma ferramenta devolveu (IDs fora disso são recusados);
- os dados dos cartões (nome, distância, horário) vêm do catálogo e do banco, nunca do texto do LLM;
- nada de preço, nota ou tempo de entrega: não temos esses dados reais;
- se o LLM falhar (cota, rede), a resposta sai no modo simples, sem erro para o usuário.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from bocado.config import get_settings
from bocado.embeddings import Embedder, get_embedder
from bocado.extraction import Extrator, ExtratorComReserva, get_extrator, rules
from bocado.llm import get_llm
from bocado.llm.base import ChamadaFerramenta, ClienteLLM, LLMIndisponivel, LLMRespostaInvalida, Mensagem, Turno, Uso
from bocado.local import cardapio
from bocado.local.cep import Local
from bocado.tools import ArgsPratos, ArgsResponder, Contexto, especificacoes, executar, sugerir_pratos
from bocado.tools import preferencias as ler_preferencias

log = logging.getLogger(__name__)

MAX_PRATOS = 4
MAX_LUGARES = 3
HISTORICO_MENSAGENS = 8

SISTEMA = """Você é o assistente do bora de quê?, um app (projeto de portfólio) que ajuda a decidir o que comer.
Usuário: {nome}, em {bairro}{cidade}/{uf}. Hoje é {hoje}.
Memória do usuário (vale sempre): restrições: {restricoes}; evita: {evitar}; gostos: {gostos}.

Fluxo do app: primeiro O QUE comer, depois ONDE comer.
- Por padrão, chame sugerir_pratos e responda com 2 a 4 pratos. NÃO traga restaurantes nesse momento.
- Só traga lugares quando o usuário pedir onde comer, um restaurante, algo perto ou aberto agora:
  use onde_comer com o prato_id de um prato já sugerido, ou buscar_lugares se ele pediu um tipo de lugar.
- Reescreva o pedido como frase completa, somando o que ainda vale da conversa
  (pediu japonês e depois "agora vegano" -> "comida japonesa vegana").
- Salve com salvar_preferencia só o que é permanente, dito como fato sobre a pessoa ("sou vegano", "não como
  porco", "tenho alergia a camarão"). Vontade do momento ("hoje quero algo sem fritura") NÃO é preferência.
- Termine SEMPRE chamando responder. Use só prato_id e lugar_id devolvidos pelas ferramentas. Nunca invente pratos,
  lugares, preços, notas ou horários. Os lugares são reais (OpenStreetMap), mas não temos cardápio, preço, nota nem
  tempo de entrega; se pedirem isso, explique.
- mensagem: português do Brasil, simpático e direto, no máximo 3 frases curtas. Chame o usuário pelo nome às vezes.
  Ao sugerir pratos, convide a tocar em "Onde comer perto" para ver os lugares.
- motivo: frase curta com o que do pedido aquilo atende (ex.: "leve, vegano e típico de Minas"). Não repita o nome.
  Para lugares, diga "confirmado" só quando o campo dieta disser "confirmado".
- Assuntos fora de comida: diga com educação que só ajuda com isso."""


@dataclass
class RespostaAgente:
    conversa_id: str
    mensagem: str
    pratos: list[dict[str, Any]]
    recomendacoes: list[dict[str, Any]]  # lugares
    modo: str  # "agente" | "simples"
    trace_id: str
    latencia_ms: int
    uso: Uso
    custo_usd: float
    passos: list[dict[str, Any]] = field(default_factory=list)
    metricas: dict[str, Any] = field(default_factory=dict)
    aviso: str | None = None


def _resumo(resultado: dict[str, Any]) -> dict[str, Any]:
    """Versão curta do resultado de uma ferramenta, para o trace."""
    r = {k: v for k, v in resultado.items() if k not in ("lugares", "pratos")}
    if "pratos" in resultado:
        r["pratos"] = [f"{p['nome']} ({p['cozinha']})" for p in resultado["pratos"]]
    if "lugares" in resultado:
        r["lugares"] = [f"{x['nome']} ({x['cozinha']}, {x['distancia_km']} km)" for x in resultado["lugares"]]
    return r


def _publico(resultado: dict[str, Any]) -> dict[str, Any]:
    """Remove chaves internas (começam com _) antes de mandar ao LLM."""
    return {k: v for k, v in resultado.items() if not k.startswith("_")}


NUMERO_PRECO = re.compile(r"R\$\s?\d")
CAMPOS_LUGAR = (
    "id",
    "nome",
    "cozinha",
    "categorias",
    "rotulos",
    "distancia_km",
    "aberto_agora",
    "horario_hoje",
    "endereco",
    "site",
    "telefone",
    "mapa_url",
    "osm_url",
    "dieta_status",
    "dieta",
    "origem_categoria",
    "sugestoes",
    "evidencia",
    "prato",
)


def cartao_lugar(lugar: dict[str, Any], motivo: str = "") -> dict[str, Any]:
    return {"lugar": {k: lugar.get(k) for k in CAMPOS_LUGAR}, "prato": lugar.get("prato"), "motivo": motivo}


def cartao_prato(prato: dict[str, Any], motivo: str = "") -> dict[str, Any]:
    return {
        **{
            k: prato.get(k)
            for k in ("id", "nome", "descricao", "categoria", "cozinha", "tags", "regional", "exige_dieta")
        },
        "motivo": motivo,
    }


def _motivo_lugar(lugar: dict[str, Any]) -> str:
    partes = [f"{r.replace('_', ' ')} {s}" for r, s in (lugar.get("dieta_status") or {}).items()]
    if lugar.get("aberto_agora"):
        partes.append("aberto agora")
    d = lugar["distancia_km"]
    partes.append(f"a {int(d * 1000)} m" if d < 1 else f"a {d:.1f} km".replace(".", ","))
    return ", ".join(partes)


class Agente:
    def __init__(
        self,
        pool: ConnectionPool,
        llm: ClienteLLM | None = None,
        embedder: Embedder | None = None,
        extrator: Extrator | None = None,
        max_passos: int | None = None,
    ):
        s = get_settings()
        self.pool = pool
        self.llm = llm if llm is not None else get_llm()
        self.embedder = embedder or get_embedder()
        self.extrator = extrator or ExtratorComReserva(get_extrator())
        self.max_passos = max_passos or s.agente_max_passos
        self.preco_in = s.preco_entrada_por_mtok
        self.preco_out = s.preco_saida_por_mtok

    # ------------------------------------------------------------ estado da conversa

    def _carregar(self, conn, visitante_id: str, conversa_id: str | None):
        historico: list[Mensagem] = []
        vistos: dict[str, dict] = {}
        pratos: dict[str, dict] = {}
        if conversa_id:
            dono = conn.execute("select visitante_id from conversas where id::text = %s", (conversa_id,)).fetchone()
            if dono is None or str(dono["visitante_id"]) != visitante_id:
                conversa_id = None
        if conversa_id:
            msgs = conn.execute(
                "select papel, conteudo, dados from mensagens where conversa_id::text = %s order by id", (conversa_id,)
            ).fetchall()
            for m in msgs:
                vistos.update(m["dados"].get("vistos", {}))
                pratos.update(m["dados"].get("pratos_vistos", {}))
            for m in msgs[-HISTORICO_MENSAGENS:]:
                texto = m["conteudo"]
                if m["papel"] == "assistant" and m["dados"].get("mostrados"):
                    texto += "\n[mostrados: " + "; ".join(m["dados"]["mostrados"]) + "]"
                historico.append(Mensagem(papel=m["papel"], texto=texto))
        else:
            conversa_id = str(uuid.uuid4())
            conn.execute("insert into conversas (id, visitante_id) values (%s, %s)", (conversa_id, visitante_id))
        return conversa_id, historico, vistos, pratos

    # ------------------------------------------------------------ API

    def responder(
        self,
        visitante_id: str,
        local: Local,
        mensagem: str,
        conversa_id: str | None = None,
        permitir_llm: bool = True,
        nome: str = "",
        area_pronta: bool = True,
    ) -> RespostaAgente:
        inicio = time.perf_counter()
        with self.pool.connection() as conn:
            conversa_id, historico, vistos, pratos = self._carregar(conn, visitante_id, conversa_id)
            ctx = Contexto(
                conn=conn,
                embedder=self.embedder,
                extrator=self.extrator,
                visitante_id=visitante_id,
                local=local,
                vistos=vistos,
                pratos_vistos=pratos,
                mensagem=mensagem,
                area_pronta=area_pronta,
            )
            aviso = None
            em_conversa = bool(historico)
            if self.llm is None or not permitir_llm:
                resp = self._modo_simples(ctx, mensagem, em_conversa)
                aviso = (
                    "Modo simples: sem LLM nesta resposta."
                    if self.llm is None
                    else "Limite de uso da demo atingido; respondendo no modo simples."
                )
            else:
                try:
                    resp = self._laco(ctx, mensagem, historico, nome)
                except (LLMIndisponivel, LLMRespostaInvalida) as e:
                    log.warning("LLM falhou (%s); modo simples", e)
                    resp = self._modo_simples(ctx, mensagem, em_conversa)
                    resp["passos"].insert(0, {"tipo": "falha_llm", "erro": str(e)[:300]})
                    aviso = "O modelo de linguagem não respondeu agora; usei o modo simples."
                aviso = resp.pop("aviso", None) or aviso

            latencia = int((time.perf_counter() - inicio) * 1000)
            uso: Uso = resp["uso"]
            custo = (uso.tokens_entrada * self.preco_in + uso.tokens_saida * self.preco_out) / 1e6
            trace_id = str(uuid.uuid4())
            mostrados = [f"prato {p['id']} {p['nome']}" for p in resp["pratos"]]
            mostrados += [f"lugar {r['lugar']['id']} {r['lugar']['nome']}" for r in resp["recomendacoes"]]
            vistos_min = {k: {c: v.get(c) for c in CAMPOS_LUGAR} for k, v in ctx.vistos.items()}
            conn.execute(
                "insert into mensagens (conversa_id, papel, conteudo) values (%s, 'user', %s)", (conversa_id, mensagem)
            )
            conn.execute(
                "insert into mensagens (conversa_id, papel, conteudo, dados) values (%s, 'assistant', %s, %s)",
                (
                    conversa_id,
                    resp["mensagem"],
                    Jsonb(
                        {
                            "vistos": vistos_min,
                            "pratos_vistos": ctx.pratos_vistos,
                            "mostrados": mostrados,
                            "trace_id": trace_id,
                        }
                    ),
                ),
            )
            conn.execute(
                """insert into traces (id, conversa_id, visitante_id, modo, latencia_ms, tokens_entrada, tokens_saida,
                   custo_usd, passos) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    trace_id,
                    conversa_id,
                    visitante_id,
                    resp["modo"],
                    latencia,
                    uso.tokens_entrada,
                    uso.tokens_saida,
                    custo,
                    Jsonb(resp["passos"]),
                ),
            )
            conn.execute("update visitantes set visto_em = now() where id = %s", (visitante_id,))

        return RespostaAgente(
            conversa_id=conversa_id,
            mensagem=resp["mensagem"],
            pratos=resp["pratos"],
            recomendacoes=resp["recomendacoes"],
            modo=resp["modo"],
            trace_id=trace_id,
            latencia_ms=latencia,
            uso=uso,
            custo_usd=custo,
            passos=resp["passos"],
            metricas=resp["metricas"],
            aviso=aviso,
        )

    # ------------------------------------------------------------ laço com LLM

    def _laco(self, ctx: Contexto, mensagem: str, historico: list[Mensagem], nome: str) -> dict[str, Any]:
        mem = ler_preferencias(ctx.conn, ctx.visitante_id)
        sistema = SISTEMA.format(
            nome=nome or "não informado",
            bairro=f"{ctx.local.bairro}, " if ctx.local.bairro else "",
            cidade=ctx.local.cidade,
            uf=ctx.local.uf,
            hoje=date.today().strftime("%d/%m/%Y"),
            restricoes=", ".join(mem["restricao"]) or "nenhuma",
            evitar=", ".join(mem["evitar"]) or "nada",
            gostos="; ".join(mem["gosto"]) or "nada registrado",
        )
        sessao = self.llm.iniciar_chat(sistema, historico, especificacoes())
        passos: list[dict[str, Any]] = []
        uso = Uso()
        metricas = {"ids_invalidos_1a_tentativa": 0, "ids_descartados": 0, "correcoes": 0, "chamadas_ferramenta": 0}

        def registrar(turno: Turno) -> Turno:
            nonlocal uso
            uso = uso + turno.uso
            passos.append(
                {
                    "tipo": "llm",
                    "latencia_ms": turno.latencia_ms,
                    "tokens_entrada": turno.uso.tokens_entrada,
                    "tokens_saida": turno.uso.tokens_saida,
                    "chamou": [c.nome for c in turno.chamadas],
                }
            )
            return turno

        estado: dict[str, Any] = {"ultimos_pratos": [], "ultimos_lugares": []}
        try:
            return self._passos(ctx, sessao, mensagem, registrar, passos, metricas, estado, lambda: uso)
        except (LLMIndisponivel, LLMRespostaInvalida) as e:
            if metricas["chamadas_ferramenta"] == 0:
                raise
            log.warning("LLM falhou no meio do turno (%s); respondendo com o que já foi feito", e)
            passos.append({"tipo": "falha_llm", "erro": str(e)[:300]})
            parcial = self._parcial(ctx, estado)
            parcial.update(
                passos=passos,
                uso=uso,
                metricas=metricas,
                aviso="O modelo de linguagem parou no meio da resposta; mostrei o que já tinha encontrado.",
            )
            return parcial

    def _passos(self, ctx, sessao, mensagem, registrar, passos, metricas, estado, uso_atual) -> dict[str, Any]:
        turno = registrar(sessao.enviar_usuario(mensagem))
        corrigiu = False
        for _ in range(self.max_passos):
            if not turno.chamadas:
                return {
                    "mensagem": (turno.texto or "Desculpe, não entendi. Pode repetir?").strip(),
                    "pratos": [],
                    "recomendacoes": [],
                    "passos": passos,
                    "uso": uso_atual(),
                    "modo": "agente",
                    "metricas": metricas,
                }
            final = next((c for c in turno.chamadas if c.nome == "responder"), None)
            if final is not None:
                pronto, retorno = self._finalizar(ctx, final, corrigiu, metricas, passos)
                if pronto:
                    retorno.update(passos=passos, uso=uso_atual(), modo="agente", metricas=metricas)
                    return retorno
                corrigiu = True
                metricas["correcoes"] += 1
                resultados = [
                    (c, retorno if c is final else {"erro": "ignorado: corrija o responder primeiro"})
                    for c in turno.chamadas
                ]
                turno = registrar(sessao.enviar_resultados(resultados))
                continue

            resultados: list[tuple[ChamadaFerramenta, dict[str, Any]]] = []
            for ch in turno.chamadas:
                res, ms = executar(ctx, ch.nome, ch.args)
                metricas["chamadas_ferramenta"] += 1
                if res.get("pratos"):
                    estado["ultimos_pratos"] = [p["id"] for p in res["pratos"]]
                if res.get("lugares"):
                    estado["ultimos_lugares"] = [x["id"] for x in res["lugares"]]
                passos.append(
                    {
                        "tipo": "ferramenta",
                        "nome": ch.nome,
                        "args": ch.args,
                        "latencia_ms": ms,
                        "resultado": _resumo(res),
                    }
                )
                resultados.append((ch, _publico(res)))
            turno = registrar(sessao.enviar_resultados(resultados))

        log.warning("agente atingiu o limite de %d passos", self.max_passos)
        passos.append({"tipo": "limite_passos"})
        parcial = self._parcial(ctx, estado)
        if parcial["pratos"] or parcial["recomendacoes"]:
            parcial.update(passos=passos, uso=uso_atual(), metricas=metricas)
            return parcial
        simples = self._modo_simples(ctx, mensagem, em_conversa=False)
        simples.update(passos=passos + simples["passos"], uso=uso_atual(), metricas=metricas)
        return simples

    def _finalizar(
        self,
        ctx: Contexto,
        chamada: ChamadaFerramenta,
        ja_corrigiu: bool,
        metricas: dict[str, Any],
        passos: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any]]:
        """Trava de grounding. Devolve (pronto, resposta) ou (False, erro para o modelo corrigir)."""
        try:
            a = ArgsResponder.model_validate(chamada.args)
        except ValidationError as e:
            if ja_corrigiu:
                return True, {"mensagem": "Separei algumas ideias pra você.", "pratos": [], "recomendacoes": []}
            return False, {"erro": f"argumentos inválidos em responder: {e.errors()[0]['msg']}"}

        pratos_invalidos = [p.prato_id for p in a.pratos if p.prato_id not in ctx.pratos_vistos]
        lugares_invalidos = [x.lugar_id for x in a.lugares if x.lugar_id not in ctx.vistos]
        invalidos = pratos_invalidos + lugares_invalidos
        if invalidos and not ja_corrigiu:
            metricas["ids_invalidos_1a_tentativa"] += len(invalidos)
            passos.append({"tipo": "grounding", "acao": "pediu_correcao", "ids_invalidos": invalidos})
            return False, {
                "erro": f"Estes IDs não vieram de nenhuma ferramenta: {invalidos}. "
                "Use só prato_id e lugar_id devolvidos pelas ferramentas."
            }
        if invalidos:
            metricas["ids_descartados"] += len(invalidos)
            passos.append({"tipo": "grounding", "acao": "descartou", "ids_invalidos": invalidos})

        pratos, vistos_p = [], set()
        for p in a.pratos:
            if p.prato_id in ctx.pratos_vistos and p.prato_id not in vistos_p and len(pratos) < MAX_PRATOS:
                pratos.append(cartao_prato(ctx.pratos_vistos[p.prato_id], p.motivo.strip()))
                vistos_p.add(p.prato_id)
        lugares, vistos_l = [], set()
        for x in a.lugares:
            if x.lugar_id in ctx.vistos and x.lugar_id not in vistos_l and len(lugares) < MAX_LUGARES:
                lugares.append(cartao_lugar(ctx.vistos[x.lugar_id], x.motivo.strip()))
                vistos_l.add(x.lugar_id)

        if NUMERO_PRECO.search(a.mensagem):
            metricas["citou_preco"] = True
            passos.append({"tipo": "grounding", "acao": "preco_inconsistente", "valores": ["mensagem cita preço"]})
        return True, {"mensagem": a.mensagem.strip(), "pratos": pratos, "recomendacoes": lugares}

    def _parcial(self, ctx: Contexto, estado: dict[str, Any]) -> dict[str, Any]:
        lugares = [
            cartao_lugar(ctx.vistos[i], _motivo_lugar(ctx.vistos[i]))
            for i in estado["ultimos_lugares"][:MAX_LUGARES]
            if i in ctx.vistos
        ]
        pratos = (
            []
            if lugares
            else [
                cartao_prato(ctx.pratos_vistos[i])
                for i in estado["ultimos_pratos"][:MAX_PRATOS]
                if i in ctx.pratos_vistos
            ]
        )
        if lugares:
            msg = "Separei estes lugares perto de você. Confira o cardápio antes de ir."
        elif pratos:
            msg = "Separei estas ideias. Toque em “Onde comer perto” para ver os lugares."
        else:
            msg = "Não encontrei nada que atenda a tudo isso. Quer afrouxar algum filtro?"
        return {"mensagem": msg, "pratos": pratos, "recomendacoes": lugares, "modo": "simples"}

    # ------------------------------------------------------------ modo simples (sem LLM)

    def _modo_simples(self, ctx: Contexto, mensagem: str, em_conversa: bool = False) -> dict[str, Any]:
        if em_conversa and not rules.extrair(mensagem).compacto() and len(mensagem.split()) <= 12:
            return {
                "mensagem": "Estou no modo simplificado agora e não consigo continuar de onde paramos. "
                'Me diga o que você quer comer, por exemplo: "algo leve" ou "comida árabe".',
                "pratos": [],
                "recomendacoes": [],
                "passos": [{"tipo": "modo_simples", "acao": "pediu_reformulacao"}],
                "uso": Uso(),
                "modo": "simples",
                "metricas": {},
            }
        inicio = time.perf_counter()
        res = sugerir_pratos(ctx, ArgsPratos(pedido=mensagem[:400], limite=MAX_PRATOS))
        ms = int((time.perf_counter() - inicio) * 1000)
        pratos = [cartao_prato(ctx.pratos_vistos[p["id"]]) for p in res["pratos"]]
        texto = (
            "Separei estas ideias. Toque em “Onde comer perto” para ver os lugares."
            if pratos
            else "Não encontrei pratos que atendam a tudo isso. Quer afrouxar algum filtro?"
        )
        passo = {
            "tipo": "ferramenta",
            "nome": "sugerir_pratos",
            "args": {"pedido": mensagem[:400]},
            "latencia_ms": ms,
            "resultado": _resumo(res),
        }
        return {
            "mensagem": texto,
            "pratos": pratos,
            "recomendacoes": [],
            "passos": [passo],
            "uso": Uso(),
            "modo": "simples",
            "metricas": {},
        }


def lugares_do_prato(pool, embedder, visitante_id: str, local: Local, prato_id: str, limite: int = 5) -> dict:
    """'Onde comer perto' do botão do cartão: busca direta, sem LLM (instantâneo e sem gastar cota)."""
    from bocado.local.busca import Origem, onde_comer

    prato = cardapio.POR_ID.get(prato_id)
    if prato is None:
        raise KeyError(prato_id)
    with pool.connection() as conn:
        mem = ler_preferencias(conn, visitante_id)
        r = onde_comer(
            conn,
            embedder,
            prato,
            Origem(local.lat, local.lon, local.uf, local.fuso),
            mem["restricao"],
            mem["evitar"],
            limite=limite,
        )
    cartoes = []
    for lugar in r.lugares:
        lugar["prato"] = prato.publico()
        cartoes.append(cartao_lugar(lugar, lugar.get("evidencia", "")))
    return {"prato": prato.publico(), "recomendacoes": cartoes, "raio_km": r.raio_km, "relaxados": r.relaxados}
