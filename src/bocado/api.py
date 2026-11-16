"""API HTTP (FastAPI) e demo web."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date
from importlib import resources
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bocado import __version__
from bocado.agent import Agente, lugares_do_prato
from bocado.config import get_settings
from bocado.db import aplicar_schema, criar_pool
from bocado.embeddings import get_embedder
from bocado.extraction import get_extrator
from bocado.local import cardapio, osm
from bocado.local import cep as cep_mod
from bocado.tools import preferencias

log = logging.getLogger("bocado.api")
WEB = resources.files("bocado").joinpath("web")


class LimitadorPorIP:
    """Janela deslizante em memória. Suficiente para uma instância de demo."""

    def __init__(self, maximo: int, janela_s: int):
        self.maximo, self.janela_s = maximo, janela_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def permitir(self, chave: str) -> bool:
        agora = time.monotonic()
        with self._lock:
            fila = self._hits.setdefault(chave, deque())
            while fila and agora - fila[0] > self.janela_s:
                fila.popleft()
            if len(fila) >= self.maximo:
                return False
            fila.append(agora)
            return True


class CotaDiaria:
    def __init__(self, maximo: int):
        self.maximo = maximo
        self._dia = date.today()
        self._usados = 0
        self._lock = threading.Lock()

    def consumir(self) -> bool:
        with self._lock:
            if date.today() != self._dia:
                self._dia, self._usados = date.today(), 0
            if self._usados >= self.maximo:
                return False
            self._usados += 1
            return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if not s.database_url:
        raise RuntimeError("DATABASE_URL não configurada. Veja .env.example.")
    pool = criar_pool(s.database_url, max_size=8)
    with pool.connection() as conn:
        aplicar_schema(conn)  # idempotente
    embedder = get_embedder()
    embedder.embed(["aquecendo"])  # carrega o modelo de embedding antes do primeiro usuário
    with pool.connection() as conn:
        sem_areas = conn.execute("select count(*) as n from areas").fetchone()["n"] == 0
    if sem_areas:
        # Banco novo: carrega os recortes salvos (capitais e regiões de teste) em segundo plano.
        from bocado.config import RAIZ

        pastas = [RAIZ / "data" / "recortes", RAIZ / "data" / "aquecimento"]
        threading.Thread(target=osm.aquecer, args=(pool, embedder, pastas), name="aquecer", daemon=True).start()
    else:
        osm.disparar_embeddings(pool, embedder)  # termina embeddings pendentes de uma subida anterior
    extrator = get_extrator()
    if hasattr(extrator, "carregar"):
        extrator.carregar()
    app.state.pool = pool
    app.state.agente = Agente(pool)
    app.state.limitador = LimitadorPorIP(s.rate_limit_per_ip, s.rate_limit_window_s)
    app.state.limitador_local = LimitadorPorIP(10, 600)
    app.state.cota = CotaDiaria(s.llm_turnos_por_dia)
    log.info("pronto: llm=%s extrator=%s", s.gemini_model if s.llm_ativo else "nenhum", extrator.nome)
    yield
    pool.close()


app = FastAPI(title="bora de quê?", version=__version__, lifespan=lifespan)

UUID_RE = r"^[0-9a-fA-F-]{36}$"


class PedidoLocal(BaseModel):
    nome: str = Field(default="", max_length=40)
    cep: str = Field(min_length=8, max_length=9)
    visitante_id: str | None = Field(default=None, pattern=UUID_RE)


class PedidoChat(BaseModel):
    visitante_id: str = Field(pattern=UUID_RE)
    mensagem: str = Field(min_length=1, max_length=500)
    conversa_id: str | None = Field(default=None, max_length=64)


def _ip(request: Request) -> str:
    encaminhado = request.headers.get("x-forwarded-for")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def _local_do_visitante(conn, visitante_id: str) -> tuple[cep_mod.Local, str] | tuple[None, None]:
    row = conn.execute(
        "select c.*, v.nome as visitante_nome from visitantes v join ceps c on c.cep = v.cep where v.id::text = %s",
        (visitante_id,),
    ).fetchone()
    if not row:
        return None, None
    local = cep_mod.Local(
        cep=row["cep"],
        logradouro=row["logradouro"],
        bairro=row["bairro"],
        cidade=row["cidade"],
        uf=row["uf"],
        ibge=row["ibge"],
        lat=row["lat"],
        lon=row["lon"],
        precisao=row["precisao"],
    )
    return local, row["visitante_nome"]


def _resumo_visitante(conn, visitante_id: str, nome: str, local: cep_mod.Local, area: dict) -> dict[str, Any]:
    return {
        "visitante_id": visitante_id,
        "nome": nome,
        "local": local.dict() | {"lat": round(local.lat, 3), "lon": round(local.lon, 3)},
        "area": area,
        "memoria": preferencias(conn, visitante_id),
        "regionais": [
            {"id": p["id"], "nome": p["nome"], "descricao": p["descricao"]}
            for p in cardapio.regionais_do_estado(local.uf)
        ],
    }


@app.get("/api/saude")
def saude(request: Request) -> dict[str, Any]:
    s = get_settings()
    with request.app.state.pool.connection() as conn:
        n = conn.execute("select count(*) as n from lugares").fetchone()["n"]
    return {
        "ok": True,
        "lugares_em_cache": n,
        "llm": s.gemini_model if s.llm_ativo else None,
        "extrator": s.extractor,
        "repo_url": s.repo_url or None,
    }


@app.post("/api/local")
def definir_local(pedido: PedidoLocal, request: Request) -> dict[str, Any]:
    """Nome + CEP. Responde assim que o CEP vira cidade; os lugares baixam em segundo plano."""
    st = request.app.state
    if not st.limitador_local.permitir(_ip(request)):
        raise HTTPException(429, "Muitas trocas de CEP em pouco tempo. Espere alguns minutos.")
    try:
        with st.pool.connection() as conn:
            local = cep_mod.resolver(pedido.cep, conn)
    except cep_mod.CepInvalido as e:
        raise HTTPException(422, "CEP inválido: use 8 dígitos.") from e
    except cep_mod.CepNaoEncontrado as e:
        raise HTTPException(404, "Não encontrei esse CEP. Confira os números.") from e
    except cep_mod.ServicoIndisponivel as e:
        log.warning("serviço de CEP indisponível: %s", e)
        raise HTTPException(503, "O serviço de CEP não respondeu agora. Tente de novo em instantes.") from e

    area = osm.iniciar_area(st.pool, get_embedder(), local.lat, local.lon)
    visitante_id = pedido.visitante_id or str(uuid.uuid4())
    nome = " ".join(pedido.nome.split())[:40]
    with st.pool.connection() as conn:
        conn.execute(
            """insert into visitantes (id, cep, nome) values (%s, %s, %s)
               on conflict (id) do update set cep = excluded.cep, nome = excluded.nome, visto_em = now()""",
            (visitante_id, local.cep, nome),
        )
        return _resumo_visitante(conn, visitante_id, nome, local, area)


@app.get("/api/visitante/{visitante_id}")
def visitante(visitante_id: str, request: Request) -> dict[str, Any]:
    st = request.app.state
    with st.pool.connection() as conn:
        local, nome = _local_do_visitante(conn, visitante_id)
        if not local:
            raise HTTPException(404, "visitante não encontrado")
        conn.execute("update visitantes set visto_em = now() where id::text = %s", (visitante_id,))
    area = osm.iniciar_area(st.pool, get_embedder(), local.lat, local.lon)  # retoma se caiu ou expirou
    with st.pool.connection() as conn:
        return _resumo_visitante(conn, visitante_id, nome, local, area)


@app.get("/api/onde-comer")
def onde(visitante_id: str, prato_id: str, request: Request) -> Any:
    """Botão 'Onde comer perto' do cartão de prato: busca direta, sem LLM."""
    st = request.app.state
    with st.pool.connection() as conn:
        local, _ = _local_do_visitante(conn, visitante_id)
    if not local:
        raise HTTPException(404, "Informe seu CEP primeiro.")
    area = osm.iniciar_area(st.pool, get_embedder(), local.lat, local.lon)
    if area["estado"] not in ("pronta", "parcial"):
        return JSONResponse(status_code=202, content={"area": area})
    try:
        return lugares_do_prato(st.pool, get_embedder(), visitante_id, local, prato_id) | {"area": area}
    except KeyError as e:
        raise HTTPException(404, "Prato não encontrado.") from e


@app.delete("/api/visitante/{visitante_id}")
def esquecer(visitante_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.pool.connection() as conn:
        conn.execute("delete from visitantes where id::text = %s", (visitante_id,))
    return {"ok": True}


@app.delete("/api/visitante/{visitante_id}/memoria")
def limpar_memoria(visitante_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.pool.connection() as conn:
        conn.execute("delete from preferencias where visitante_id::text = %s", (visitante_id,))
    return {"ok": True}


@app.post("/api/chat")
def chat(pedido: PedidoChat, request: Request) -> dict[str, Any]:
    st = request.app.state
    if not st.limitador.permitir(_ip(request)):
        raise HTTPException(429, "Muitas mensagens em pouco tempo. Espere alguns minutos e tente de novo.")
    with st.pool.connection() as conn:
        local, nome = _local_do_visitante(conn, pedido.visitante_id)
    if not local:
        raise HTTPException(404, "Informe seu CEP primeiro.")
    area = osm.iniciar_area(st.pool, get_embedder(), local.lat, local.lon)
    permitir_llm = st.cota.consumir()
    r = st.agente.responder(
        pedido.visitante_id,
        local,
        pedido.mensagem.strip(),
        pedido.conversa_id,
        permitir_llm,
        nome=nome,
        area_pronta=area["estado"] == "pronta",
    )
    d = asdict(r)
    d["custo_usd"] = round(d["custo_usd"], 6)
    d["area"] = area
    return d


@app.get("/api/traces/{trace_id}")
def trace(trace_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.pool.connection() as conn:
        t = conn.execute("select * from traces where id::text = %s", (trace_id,)).fetchone()
    if not t:
        raise HTTPException(404, "trace não encontrado")
    return {
        **t,
        "id": str(t["id"]),
        "conversa_id": str(t["conversa_id"]),
        "visitante_id": str(t["visitante_id"]),
        "criado_em": t["criado_em"].isoformat(),
    }


@app.exception_handler(Exception)
async def erro_inesperado(request: Request, exc: Exception):
    log.exception("erro não tratado em %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro interno. Tente de novo em instantes."})


class _EstaticosRevalidados(StaticFiles):
    """Sem Cache-Control o navegador guarda o JS/CSS por heurística e serve versão velha após um deploy.
    `no-cache` faz revalidar pelo ETag: custa um 304, nunca um arquivo desatualizado."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _EstaticosRevalidados(directory=str(WEB)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(WEB.joinpath("index.html")), headers={"Cache-Control": "no-cache"})
