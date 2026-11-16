"""LLM roteirizado para testes: devolve turnos pré-definidos e registra o que recebeu."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bocado.llm.base import ChamadaFerramenta, Ferramenta, LLMIndisponivel, Mensagem, Turno, Uso

Roteiro = Callable[[list[Any]], Turno]


class LLMFalso:
    provedor = "falso"
    modelo = "roteiro"

    def __init__(self, turnos: list[Turno | Exception] | None = None, json_saida: str = "{}"):
        self._turnos = list(turnos or [])
        self.json_saida = json_saida
        self.recebido: list[Any] = []
        self.sistema: str | None = None

    def iniciar_chat(self, sistema: str, historico: list[Mensagem], ferramentas: list[Ferramenta]) -> SessaoFalsa:
        self.sistema = sistema
        return SessaoFalsa(self)

    def proximo(self, entrada: Any) -> Turno:
        self.recebido.append(entrada)
        if not self._turnos:
            raise LLMIndisponivel("roteiro acabou")
        t = self._turnos.pop(0)
        if isinstance(t, Exception):
            raise t
        return t

    def gerar_json(self, sistema: str, usuario: str, schema: dict[str, Any]) -> tuple[str, Uso, int]:
        self.recebido.append(usuario)
        return self.json_saida, Uso(100, 20), 5


class SessaoFalsa:
    def __init__(self, llm: LLMFalso):
        self._llm = llm

    def enviar_usuario(self, texto: str) -> Turno:
        return self._llm.proximo(texto)

    def enviar_resultados(self, resultados: list[tuple[ChamadaFerramenta, dict[str, Any]]]) -> Turno:
        return self._llm.proximo(resultados)


def chamada(nome: str, **args: Any) -> Turno:
    return Turno(
        texto=None,
        chamadas=[ChamadaFerramenta(nome=nome, args=args, id=f"id-{nome}")],
        uso=Uso(500, 50),
        latencia_ms=10,
    )
