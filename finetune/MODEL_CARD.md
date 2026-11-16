---
language: pt
license: apache-2.0
base_model: Qwen/Qwen2.5-0.5B-Instruct
tags:
  - lora
  - structured-output
  - information-extraction
  - food-delivery
pipeline_tag: text-generation
---

# bocado-extrator

Qwen2.5-0.5B-Instruct ajustado com LoRA para transformar pedidos de comida em português em filtros JSON
(categoria, preço máximo, restrições, o que evitar, atributos, distância, tempo e ordenação).
Faz parte do projeto [bora de quê?]({{REPO_URL}}), um agente que sugere pratos e mostra lugares reais perto do CEP.

## Uso

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

tok = AutoTokenizer.from_pretrained("{{MODEL_ID}}")
modelo = AutoModelForCausalLM.from_pretrained("{{MODEL_ID}}")
msgs = [
    {"role": "system", "content": "Extraia os filtros do pedido de comida. Responda apenas com JSON."},
    {"role": "user", "content": "quero algo leve e barato, perto de casa, nada de fritura"},
]
ids = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt")
saida = modelo.generate(ids, max_new_tokens=96, do_sample=False)
print(tok.decode(saida[0, ids.shape[1] :], skip_special_tokens=True))
# {"evitar": ["fritura"], "atributos": ["leve"], "distancia_max_km": 3, "ordenar_por": "menor_preco"}
```

A saída traz só os campos fora do padrão. O esquema e as regras de rotulagem estão em
`docs/guia-de-rotulagem.md` no repositório.

## Treino

- Dados: 2.600 exemplos sintéticos gerados por templates (`finetune/gerar_dataset.py`), com rótulo correto por
  construção, ruído de digitação, gírias e frases sem acento. 200 para validação, 200 para teste.
- LoRA rank 16 em todas as camadas, perda só na resposta, {{ITERS}} iterações com batch 16 (500 com lr 2e-5, depois continuação com lr 3e-5).
- Hardware: MacBook com Apple M4 (16 GB), via `mlx-lm`. Tempo de treino: {{TEMPO_TREINO}}.

## Avaliação

Medido em 100 pedidos escritos à mão que o modelo nunca viu (`evals/cases/pedidos.jsonl`), rodando em CPU/MPS
com `transformers` e decodificação gulosa:

{{TABELA}}

## Limitações

- O vocabulário de saída é fechado (17 categorias, 4 restrições, 5 itens a evitar, 4 atributos).
  Pedidos fora disso (ex.: "sem cebola") não viram filtro.
- Treinado em texto sintético de templates: estilos de escrita muito diferentes podem degradar o resultado.
  O conjunto desafio (`evals/cases/desafio.jsonl`) mede isso.
- Projeto de portfólio, não usado em produção.
