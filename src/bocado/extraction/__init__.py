"""Extratores: texto do pedido -> Filtros.

Três implementações com a mesma interface, comparadas em evals/run_extracao.py:
- rules:     léxico e regex (baseline, sem modelo)
- llm:       modelo grande via API, com saída estruturada
- finetuned: modelo pequeno aberto ajustado com LoRA, rodando local
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from bocado.extraction import rules
from bocado.extraction.schema import ErroDeParse, Filtros, json_schema_filtros, parse_filtros

log = logging.getLogger(__name__)

# Prompt do modelo pequeno. Treino e inferência usam exatamente este texto.
SISTEMA_MODELO_PEQUENO = "Extraia os filtros do pedido de comida. Responda apenas com JSON."

SISTEMA_LLM = """Você converte pedidos de comida em português em filtros JSON para um app de delivery.
Rotule só o que está escrito. Siga estas convenções:
- categorias: cozinha citada ou prato típico dela (sushi/ramen->japonesa, yakisoba->chinesa, pad thai->tailandesa,
  massa/lasanha/risoto/nhoque->italiana, esfiha/kibe/falafel->arabe, taco/burrito/quesadilla->mexicana,
  feijoada/PF/marmita/comida caseira->brasileira, baião/tapioca/cuscuz/moqueca/acarajé->nordestina,
  picanha/costela->churrasco, sanduíche/pastel/coxinha/x-salada->lanches, sobremesa/brigadeiro/bolo/brownie->doces,
  padaria/café da manhã->cafe). Prato que existe em várias cozinhas (curry, pão de queijo) ou ingrediente sozinho
  (camarão, tofu, peixe) não vira categoria. "Doce" como adjetivo (tapioca doce) não vira doces.
- preco_max: teto explícito em reais ("até 40", "uns 30 conto", "menos de 50" -> 50). "500 ml" e "pra 4 pessoas" não são preço.
- restricoes: vegetariano ("sem carne", "não como carne"), vegano, sem_gluten (celíaco), sem_lactose (intolerante, sem leite).
  Vegano não acrescenta vegetariano.
- evitar: fritura (sem fritura, nada frito), carne_vermelha, porco (inclui bacon), frutos_do_mar (inclui peixe e camarão),
  pimenta (sem pimenta, nada picante).
- atributos: leve (leve, light, saudável, fit, nada pesado, de dieta), proteico (proteína, pós-treino),
  apimentado (picante, com pimenta), para_compartilhar (2 ou mais pessoas, dividir, família, galera).
- distancia_max_km: número explícito; vago (perto, no bairro, da região, não muito longe) -> 3.
- tempo_max_min: explícito em minutos (meia hora=30, uma hora=60); urgência vaga (rápido, pressa, que não demore) -> 30.
- ordenar_por: menor_preco (barato, em conta, tô liso) > avaliacao (bem avaliado, o melhor) > entrega_rapida
  (só o superlativo "o que chegar mais rápido", e aí tempo_max_min fica null). Padrão: relevancia.
Campos sem informação: lista vazia ou null."""


MODELO_BASE = "Qwen/Qwen2.5-0.5B-Instruct"
SISTEMA_BASE = (
    SISTEMA_LLM
    + """
Responda apenas com um objeto JSON com estas chaves: categorias, preco_max, restricoes, evitar, atributos,
distancia_max_km, tempo_max_min, ordenar_por. Use só os valores permitidos acima."""
)


@dataclass
class ResultadoExtracao:
    filtros: Filtros
    valido: bool = True
    latencia_ms: float = 0.0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    bruto: str = ""
    erro: str | None = None
    extras: dict = field(default_factory=dict)


class Extrator(Protocol):
    nome: str

    def extrair(self, texto: str) -> ResultadoExtracao: ...


class ExtratorRegras:
    nome = "rules"

    def extrair(self, texto: str) -> ResultadoExtracao:
        inicio = time.perf_counter()
        f = rules.extrair(texto)
        return ResultadoExtracao(filtros=f, latencia_ms=(time.perf_counter() - inicio) * 1000, bruto=f.json_compacto())


class ExtratorLLM:
    nome = "llm"

    def __init__(self, cliente):
        self._c = cliente
        self.modelo = cliente.modelo

    def extrair(self, texto: str) -> ResultadoExtracao:
        bruto, uso, ms = self._c.gerar_json(SISTEMA_LLM, texto, json_schema_filtros())
        try:
            f, valido, erro = parse_filtros(bruto), True, None
        except ErroDeParse as e:
            f, valido, erro = rules.extrair(texto), False, str(e)
        return ResultadoExtracao(
            filtros=f,
            valido=valido,
            latencia_ms=ms,
            tokens_entrada=uso.tokens_entrada,
            tokens_saida=uso.tokens_saida,
            bruto=bruto,
            erro=erro,
        )


class ExtratorAjustado:
    """Modelo pequeno ajustado (Qwen2.5-0.5B + LoRA fundido), rodando com transformers.

    Com sistema=SISTEMA_BASE e o modelo base, vira a linha de base "mesmo modelo, sem ajuste".
    """

    def __init__(
        self, caminho: str, max_novos_tokens: int = 96, sistema: str = SISTEMA_MODELO_PEQUENO, nome: str = "finetuned"
    ):
        self.caminho = caminho
        self.max_novos_tokens = max_novos_tokens
        self.sistema = sistema
        self.nome = nome
        self._modelo = None
        self._tok = None
        self._device = "cpu"
        self._lock = threading.Lock()

    def carregar(self) -> None:
        with self._lock:
            if self._modelo is not None:
                return
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._device = "mps" if torch.backends.mps.is_available() else "cpu"
            log.info("carregando modelo de extração %s em %s", self.caminho, self._device)
            self._tok = AutoTokenizer.from_pretrained(self.caminho)
            self._modelo = AutoModelForCausalLM.from_pretrained(self.caminho, dtype=torch.float32)
            self._modelo.to(self._device).eval()

    def prompt(self, texto: str) -> str:
        msgs = [{"role": "system", "content": self.sistema}, {"role": "user", "content": texto}]
        return self._tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    def extrair(self, texto: str) -> ResultadoExtracao:
        import torch

        self.carregar()
        inicio = time.perf_counter()
        entrada = self._tok(self.prompt(texto), return_tensors="pt").to(self._device)
        with self._lock, torch.inference_mode():
            saida = self._modelo.generate(
                **entrada, max_new_tokens=self.max_novos_tokens, do_sample=False, pad_token_id=self._tok.eos_token_id
            )
        novos = saida[0, entrada["input_ids"].shape[1] :]
        bruto = self._tok.decode(novos, skip_special_tokens=True)
        ms = (time.perf_counter() - inicio) * 1000
        try:
            f, valido, erro = parse_filtros(bruto), True, None
        except ErroDeParse as e:
            f, valido, erro = rules.extrair(texto), False, str(e)
        return ResultadoExtracao(
            filtros=f,
            valido=valido,
            latencia_ms=ms,
            tokens_entrada=int(entrada["input_ids"].shape[1]),
            tokens_saida=int(len(novos)),
            bruto=bruto,
            erro=erro,
        )


_cache: dict[str, Extrator] = {}


def get_extrator(nome: str | None = None) -> Extrator:
    from bocado.config import get_settings

    s = get_settings()
    nome = nome or s.extractor
    if nome not in _cache:
        if nome == "rules":
            _cache[nome] = ExtratorRegras()
        elif nome == "llm":
            from bocado.llm import get_llm

            cliente = get_llm()
            if cliente is None:
                raise RuntimeError("EXTRACTOR=llm exige GEMINI_API_KEY")
            _cache[nome] = ExtratorLLM(cliente)
        elif nome == "finetuned":
            _cache[nome] = ExtratorAjustado(s.finetuned_model)
        elif nome == "base":
            _cache[nome] = ExtratorAjustado(MODELO_BASE, max_novos_tokens=160, sistema=SISTEMA_BASE, nome="base")
        else:
            raise ValueError(f"extrator desconhecido: {nome}")
    return _cache[nome]


class ExtratorComReserva:
    """Usa o extrator principal e, se ele falhar (modelo fora do ar, cota), cai nas regras."""

    def __init__(self, principal: Extrator):
        self.principal = principal
        self.nome = principal.nome
        self._regras = ExtratorRegras()

    def extrair(self, texto: str) -> ResultadoExtracao:
        try:
            return self.principal.extrair(texto)
        except Exception as e:  # noqa: BLE001 - qualquer falha do modelo vira reserva, com log
            log.warning("extrator %s falhou (%s); usando regras", self.principal.nome, e)
            r = self._regras.extrair(texto)
            r.erro = f"reserva: {e}"
            return r
