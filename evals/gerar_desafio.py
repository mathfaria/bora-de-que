"""Gera os TEXTOS do conjunto desafio com o Gemini (os rótulos são feitos à mão depois).

Por que existe: o conjunto principal foi escrito pela mesma pessoa que escreveu o léxico das regras
e os templates do ajuste fino, então mede pouco a robustez a outros estilos de escrita. Aqui um
LLM escreve pedidos em estilos variados; a rotulagem segue docs/guia-de-rotulagem.md e é revisada
por humano. O LLM não propõe rótulos, para não favorecer o extrator via LLM na comparação.

    python evals/gerar_desafio.py   -> evals/cases/desafio_textos.json
"""

from __future__ import annotations

import json

from bocado.config import RAIZ
from bocado.llm import get_llm

PROMPT = """Escreva 80 mensagens diferentes que pessoas reais mandariam para um app de delivery de comida no
Brasil, pedindo o que querem comer. Varie muito o estilo:
- adolescente com gíria e abreviação; pessoa mais velha e formal; mensagem ditada por áudio, sem pontuação;
- mensagem longa contando um contexto (dia cansativo, visita chegando, pós-academia, ressaca, dieta);
- pedidos indiretos ("algo que não pese", "comida de conforto"), com erros de digitação, com emoji;
- pedidos curtíssimos de duas ou três palavras.
Cubra, espalhados entre as mensagens: tipos de cozinha ou pratos (japonesa, pizza, hambúrguer, árabe, mexicana,
nordestina, churrasco, saladas, açaí, doces, café da manhã, comida caseira, chinesa, tailandesa, italiana, poke,
lanches), limites de preço em reais, restrições (vegetariano, vegano, sem glúten, sem lactose), coisas a evitar
(fritura, carne vermelha, porco, frutos do mar, pimenta), desejo de algo leve, proteico, apimentado ou para
dividir com mais pessoas, distância, pressa e preferência por barato ou bem avaliado. Muitas mensagens devem
combinar 2 ou 3 desses pontos; umas 8 não devem pedir nada específico.
Não numere. Responda com JSON: {"mensagens": ["...", "..."]}"""

SCHEMA = {
    "type": "object",
    "properties": {"mensagens": {"type": "array", "items": {"type": "string"}}},
    "required": ["mensagens"],
}


def main() -> None:
    llm = get_llm()
    if llm is None:
        raise SystemExit("Configure GEMINI_API_KEY no .env")
    bruto, uso, ms = llm.gerar_json("Você escreve dados de teste realistas em português do Brasil.", PROMPT, SCHEMA)
    msgs = json.loads(bruto)["mensagens"]
    saida = RAIZ / "evals" / "cases" / "desafio_textos.json"
    saida.write_text(
        json.dumps({"modelo": llm.modelo, "prompt": PROMPT, "mensagens": msgs}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"{len(msgs)} mensagens ({uso.tokens_entrada}+{uso.tokens_saida} tokens, {ms} ms) -> {saida}")


if __name__ == "__main__":
    main()
