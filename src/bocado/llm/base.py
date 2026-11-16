"""Interface mínima de LLM usada pelo agente e pelos extratores.

O agente fala só com estas classes. Trocar de provedor é escrever outro adaptador.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMIndisponivel(Exception):
    """Cota, timeout, erro de rede ou 5xx depois das novas tentativas. O agente cai no modo simples."""


class LLMRespostaInvalida(Exception):
    """O modelo respondeu algo que não conseguimos usar (bloqueio de segurança, vazio, JSON quebrado)."""


@dataclass
class Uso:
    tokens_entrada: int = 0
    tokens_saida: int = 0

    def __add__(self, outro: Uso) -> Uso:
        return Uso(self.tokens_entrada + outro.tokens_entrada, self.tokens_saida + outro.tokens_saida)


@dataclass
class ChamadaFerramenta:
    nome: str
    args: dict[str, Any]
    id: str | None = None


@dataclass
class Turno:
    texto: str | None
    chamadas: list[ChamadaFerramenta] = field(default_factory=list)
    uso: Uso = field(default_factory=Uso)
    latencia_ms: int = 0


@dataclass
class Ferramenta:
    nome: str
    descricao: str
    parametros: dict[str, Any]  # JSON Schema


@dataclass
class Mensagem:
    papel: str  # "user" | "assistant"
    texto: str


class SessaoChat(Protocol):
    def enviar_usuario(self, texto: str) -> Turno: ...

    def enviar_resultados(self, resultados: list[tuple[ChamadaFerramenta, dict[str, Any]]]) -> Turno: ...


class ClienteLLM(Protocol):
    provedor: str
    modelo: str

    def iniciar_chat(self, sistema: str, historico: list[Mensagem], ferramentas: list[Ferramenta]) -> SessaoChat: ...

    def gerar_json(self, sistema: str, usuario: str, schema: dict[str, Any]) -> tuple[str, Uso, int]: ...
