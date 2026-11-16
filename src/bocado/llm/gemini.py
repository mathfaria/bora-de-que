"""Adaptador para a API Gemini (SDK oficial google-genai)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from bocado.llm.base import (
    ChamadaFerramenta,
    Ferramenta,
    LLMIndisponivel,
    LLMRespostaInvalida,
    Mensagem,
    Turno,
    Uso,
)

log = logging.getLogger(__name__)


def _atraso_sugerido(e: errors.APIError) -> float | None:
    """Lê o RetryInfo que a API manda junto com o 429 (ex.: '37s')."""
    detalhes = getattr(e, "details", None) or {}
    try:
        for d in detalhes.get("error", {}).get("details", []):
            if "retryDelay" in d:
                return float(re.sub(r"[^0-9.]", "", d["retryDelay"]) or 0)
    except (AttributeError, TypeError, ValueError):
        pass
    return None


class ClienteGemini:
    provedor = "gemini"

    def __init__(
        self,
        api_key: str,
        modelo: str,
        timeout_s: float = 40.0,
        thinking_level: str = "low",
        espera_max_s: float = 8.0,
        tentativas: int = 3,
    ):
        if not api_key:
            raise ValueError("GEMINI_API_KEY vazia")
        self.modelo = modelo
        self.thinking_level = thinking_level.strip().upper()
        self.espera_max_s = espera_max_s
        self.tentativas = tentativas
        self._cliente = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self._thinking_suportado = True

    # ---------- infraestrutura ----------

    def _config(self, **kw: Any) -> types.GenerateContentConfig:
        if self.thinking_level and self._thinking_suportado:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=self.thinking_level)
        return types.GenerateContentConfig(**kw)

    def _chamar(self, contents: list[types.Content], **config: Any) -> tuple[types.GenerateContentResponse, int]:
        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.tentativas + 1):
            inicio = time.perf_counter()
            try:
                resp = self._cliente.models.generate_content(
                    model=self.modelo, contents=contents, config=self._config(**config)
                )
                return resp, int((time.perf_counter() - inicio) * 1000)
            except errors.ClientError as e:
                ultimo_erro = e
                if e.code == 400 and self._thinking_suportado and "thinking" in str(e).lower():
                    log.warning("modelo %s não aceita thinking_level; seguindo sem", self.modelo)
                    self._thinking_suportado = False
                    continue
                if e.code == 429:
                    atraso = _atraso_sugerido(e) or 2.0 * tentativa
                    if atraso > self.espera_max_s or tentativa == self.tentativas:
                        raise LLMIndisponivel(f"cota do Gemini esgotada (429), nova tentativa em {atraso:.0f}s") from e
                    log.info("429 do Gemini; aguardando %.1fs", atraso)
                    time.sleep(atraso)
                    continue
                raise LLMIndisponivel(f"erro {e.code} do Gemini: {e.message}") from e
            except errors.ServerError as e:
                ultimo_erro = e
                log.warning("erro %s do Gemini (tentativa %d)", e.code, tentativa)
                time.sleep(min(1.5 * tentativa, self.espera_max_s))
            except (httpx.TimeoutException, httpx.TransportError) as e:
                ultimo_erro = e
                log.warning("falha de rede com o Gemini (tentativa %d): %s", tentativa, e)
        raise LLMIndisponivel(f"Gemini indisponível: {ultimo_erro}") from ultimo_erro

    @staticmethod
    def _uso(resp: types.GenerateContentResponse) -> Uso:
        u = resp.usage_metadata
        if u is None:
            return Uso()
        saida = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
        return Uso(tokens_entrada=u.prompt_token_count or 0, tokens_saida=saida)

    @staticmethod
    def _conteudo(resp: types.GenerateContentResponse) -> types.Content:
        if not resp.candidates:
            motivo = resp.prompt_feedback.block_reason if resp.prompt_feedback else "sem candidatos"
            raise LLMRespostaInvalida(f"resposta vazia do Gemini ({motivo})")
        cand = resp.candidates[0]
        if cand.content is None or not cand.content.parts:
            raise LLMRespostaInvalida(f"resposta sem conteúdo (finish_reason={cand.finish_reason})")
        return cand.content

    # ---------- API pública ----------

    def iniciar_chat(self, sistema: str, historico: list[Mensagem], ferramentas: list[Ferramenta]) -> SessaoGemini:
        return SessaoGemini(self, sistema, historico, ferramentas)

    def gerar_json(self, sistema: str, usuario: str, schema: dict[str, Any]) -> tuple[str, Uso, int]:
        resp, ms = self._chamar(
            [types.Content(role="user", parts=[types.Part(text=usuario)])],
            system_instruction=sistema,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0.0,
        )
        conteudo = self._conteudo(resp)
        texto = "".join(p.text or "" for p in conteudo.parts if not p.thought)
        return texto, self._uso(resp), ms


class SessaoGemini:
    def __init__(self, cliente: ClienteGemini, sistema: str, historico: list[Mensagem], ferramentas: list[Ferramenta]):
        self._c = cliente
        self._sistema = sistema
        self._ferramentas = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(name=f.nome, description=f.descricao, parameters_json_schema=f.parametros)
                    for f in ferramentas
                ]
            )
        ]
        self._contents: list[types.Content] = [
            types.Content(role="user" if m.papel == "user" else "model", parts=[types.Part(text=m.texto)])
            for m in historico
        ]

    def _passo(self) -> Turno:
        resp, ms = self._c._chamar(
            self._contents,
            system_instruction=self._sistema,
            tools=self._ferramentas,
            # ANY obriga o modelo a sempre chamar uma ferramenta; a ferramenta "responder" encerra o turno.
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.3,
        )
        conteudo = self._c._conteudo(resp)
        # Guarda o conteúdo do modelo como veio (inclui as assinaturas de raciocínio exigidas pela API).
        self._contents.append(conteudo)
        chamadas = [
            ChamadaFerramenta(nome=p.function_call.name, args=dict(p.function_call.args or {}), id=p.function_call.id)
            for p in conteudo.parts
            if p.function_call is not None
        ]
        texto = "".join(p.text or "" for p in conteudo.parts if p.text and not p.thought) or None
        return Turno(texto=texto, chamadas=chamadas, uso=self._c._uso(resp), latencia_ms=ms)

    def enviar_usuario(self, texto: str) -> Turno:
        self._contents.append(types.Content(role="user", parts=[types.Part(text=texto)]))
        return self._passo()

    def enviar_resultados(self, resultados: list[tuple[ChamadaFerramenta, dict[str, Any]]]) -> Turno:
        partes = [
            types.Part(function_response=types.FunctionResponse(id=ch.id, name=ch.nome, response=res))
            for ch, res in resultados
        ]
        self._contents.append(types.Content(role="user", parts=partes))
        return self._passo()
