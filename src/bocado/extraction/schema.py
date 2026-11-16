"""Esquema dos filtros estruturados extraídos de um pedido em linguagem natural.

As regras de rotulagem (o que cada expressão vira) estão em docs/guia-de-rotulagem.md.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

CATEGORIAS = (
    "acai",
    "arabe",
    "brasileira",
    "cafe",
    "chinesa",
    "churrasco",
    "doces",
    "hamburguer",
    "italiana",
    "japonesa",
    "lanches",
    "mexicana",
    "nordestina",
    "pizza",
    "poke",
    "saladas",
    "tailandesa",
)
RESTRICOES = ("vegetariano", "vegano", "sem_gluten", "sem_lactose")
EVITAR = ("fritura", "carne_vermelha", "porco", "frutos_do_mar", "pimenta")
ATRIBUTOS = ("leve", "proteico", "apimentado", "para_compartilhar")
ORDENACOES = ("relevancia", "menor_preco", "avaliacao", "entrega_rapida")

Categoria = Literal[CATEGORIAS]  # type: ignore[valid-type]
Restricao = Literal[RESTRICOES]  # type: ignore[valid-type]
Evitar = Literal[EVITAR]  # type: ignore[valid-type]
Atributo = Literal[ATRIBUTOS]  # type: ignore[valid-type]
Ordenacao = Literal[ORDENACOES]  # type: ignore[valid-type]

CAMPOS = (
    "categorias",
    "preco_max",
    "restricoes",
    "evitar",
    "atributos",
    "distancia_max_km",
    "tempo_max_min",
    "ordenar_por",
)


class Filtros(BaseModel):
    categorias: list[Categoria] = Field(default_factory=list)
    preco_max: float | None = Field(default=None, gt=0)
    restricoes: list[Restricao] = Field(default_factory=list)
    evitar: list[Evitar] = Field(default_factory=list)
    atributos: list[Atributo] = Field(default_factory=list)
    distancia_max_km: float | None = Field(default=None, gt=0)
    tempo_max_min: int | None = Field(default=None, gt=0)
    ordenar_por: Ordenacao = "relevancia"

    @field_validator("categorias", "restricoes", "evitar", "atributos")
    @classmethod
    def _ordenar_unicos(cls, v: list[str]) -> list[str]:
        return sorted(set(v))

    @field_validator("preco_max", "distancia_max_km")
    @classmethod
    def _arredondar(cls, v: float | None) -> float | None:
        return None if v is None else round(float(v), 2)

    def compacto(self) -> dict[str, Any]:
        """Só os campos fora do padrão, na ordem fixa de CAMPOS. É o formato de saída do modelo."""
        d = self.model_dump()
        padrao = Filtros().model_dump()
        out = {}
        for k in CAMPOS:
            if d[k] != padrao[k]:
                v = d[k]
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                out[k] = v
        return out

    def json_compacto(self) -> str:
        return json.dumps(self.compacto(), ensure_ascii=False, separators=(", ", ": "))

    def mesclar_memoria(self, restricoes: list[str], evitar: list[str]) -> Filtros:
        """Soma as preferências salvas do usuário (são restrições duras)."""
        return self.model_copy(
            update={
                "restricoes": sorted(set(self.restricoes) | {r for r in restricoes if r in RESTRICOES}),
                "evitar": sorted(set(self.evitar) | {e for e in evitar if e in EVITAR}),
            }
        )


def json_schema_filtros() -> dict[str, Any]:
    """JSON Schema simples (sem $ref) para saída estruturada de LLM."""

    def lista(valores):
        return {"type": "array", "items": {"type": "string", "enum": list(valores)}}

    return {
        "type": "object",
        "properties": {
            "categorias": lista(CATEGORIAS),
            "preco_max": {"type": ["number", "null"]},
            "restricoes": lista(RESTRICOES),
            "evitar": lista(EVITAR),
            "atributos": lista(ATRIBUTOS),
            "distancia_max_km": {"type": ["number", "null"]},
            "tempo_max_min": {"type": ["integer", "null"]},
            "ordenar_por": {"type": "string", "enum": list(ORDENACOES)},
        },
        "required": list(CAMPOS),
    }


class ErroDeParse(ValueError):
    pass


def parse_filtros(texto: str) -> Filtros:
    """Lê a saída de um modelo: tolera cercas de código e texto ao redor, exige JSON válido no esquema."""
    t = texto.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    inicio, fim = t.find("{"), t.rfind("}")
    if inicio == -1 or fim == -1:
        raise ErroDeParse(f"sem objeto JSON: {texto[:80]!r}")
    try:
        dados = json.loads(t[inicio : fim + 1])
    except json.JSONDecodeError as e:
        raise ErroDeParse(f"JSON inválido: {e}") from e
    if not isinstance(dados, dict):
        raise ErroDeParse("JSON não é objeto")
    desconhecidas = set(dados) - set(CAMPOS)
    if desconhecidas:
        raise ErroDeParse(f"chaves fora do esquema: {sorted(desconhecidas)}")
    try:
        return Filtros.model_validate({k: v for k, v in dados.items() if k in CAMPOS and v is not None})
    except ValidationError as e:
        raise ErroDeParse(f"fora do esquema: {e.errors()[0]['msg']}") from e
