# Guia de rotulagem dos filtros

Este guia define como um pedido em linguagem natural vira filtros estruturados. Ele vale para os três lugares onde rótulos aparecem: o conjunto de referência (`evals/cases/pedidos.jsonl`), o gerador do dataset de ajuste fino (`finetune/gerar_dataset.py`) e o extrator por regras (`src/bocado/extraction/rules.py`).

Regra geral: **rotule só o que está no texto**. Preferências salvas do usuário (ex.: "sou vegetariana" dito numa conversa anterior) entram depois, na hora da busca, e não fazem parte do rótulo.

## Campos

| Campo | Valores | Quando preencher |
|---|---|---|
| `categorias` | `acai, arabe, brasileira, cafe, chinesa, churrasco, doces, hamburguer, italiana, japonesa, lanches, mexicana, nordestina, pizza, poke, saladas, tailandesa` | O pedido cita a cozinha ou um prato típico dela |
| `preco_max` | número (R$) | Há um teto explícito de preço |
| `restricoes` | `vegetariano, vegano, sem_gluten, sem_lactose` | Restrição alimentar dita no texto |
| `evitar` | `fritura, carne_vermelha, porco, frutos_do_mar, pimenta` | Algo que o usuário não quer |
| `atributos` | `leve, proteico, apimentado, para_compartilhar` | Característica desejada do prato |
| `distancia_max_km` | número (km) | Limite de distância |
| `tempo_max_min` | inteiro (min) | Limite de tempo de entrega |
| `ordenar_por` | `relevancia, menor_preco, avaliacao, entrega_rapida` | Critério de ordenação |

## Convenções

**Categorias**
- Prato típico vira a categoria dele: sushi, temaki, ramen → `japonesa`; yakisoba → `chinesa`; pad thai → `tailandesa`; massa, macarrão, lasanha, risoto, nhoque → `italiana`; esfiha, kibe, falafel → `arabe`; taco, burrito, quesadilla → `mexicana`; feijoada, PF, marmita, comida caseira → `brasileira`; baião, tapioca, cuscuz, moqueca, acarajé → `nordestina`; picanha, costela → `churrasco`; sanduíche, pastel, coxinha, x-salada → `lanches`; sobremesa, brigadeiro, bolo, brownie → `doces`; padaria, café da manhã → `cafe`.
- Prato que existe em várias cozinhas (curry, pão de queijo) **não** vira categoria; a busca semântica cuida dele.
- Ingrediente sozinho (camarão, cogumelo, tofu, peixe) **não** vira categoria.
- "Doce" como adjetivo de outro prato ("tapioca doce") não vira `doces`.
- Mais de uma cozinha ("chinesa ou japonesa") vira lista com as duas.

**Preço**
- "até 40", "no máximo R$ 35", "uns 30 reais", "trinta conto", "menos de 50" → o número (50 no último caso).
- O valor vale para o pedido inteiro, inclusive em "pizza pra dois até 70".
- "500 ml", "20 peças", "pra 4 pessoas" não são preço.

**Restrições**
- "sem carne" e "não como carne" → `vegetariano`. "Sem carne vermelha" é outra coisa (ver `evitar`).
- "Celíaco" → `sem_gluten`. "Intolerante à lactose", "sem leite" → `sem_lactose`.
- "Vegano" não acrescenta `vegetariano`: rotula-se só o que foi dito; a busca sabe que vegano é mais restrito.

**Evitar**
- "sem fritura", "nada frito", "não quero empanado" → `fritura`.
- "sem carne vermelha", "evito carne de boi" → `carne_vermelha`.
- "não como porco", "sem bacon" → `porco`.
- "sem peixe", "alergia a camarão", "sem frutos do mar" → `frutos_do_mar`.
- "sem pimenta", "nada picante" → `pimenta`.

**Atributos**
- `leve`: leve, levinho, light, saudável, fit, "nada pesado", "tô de dieta".
- `proteico`: proteína, proteico, pós-treino.
- `apimentado`: apimentado, picante, ardido, "com pimenta".
- `para_compartilhar`: duas ou mais pessoas ("pra dois", "pra 4 pessoas", "pra família", "pra galera", "pra dividir", "jantar a dois").

**Distância e tempo**
- Distância explícita ("até 2 km") → o número. Vaga ("perto", "aqui perto", "no bairro", "da região", "não muito longe") → `3`.
- Tempo explícito ("em 40 min", "meia hora", "uma hora") → o número em minutos. Urgência vaga ("rápido", "com pressa", "urgente", "que não demore") → `30`.

**Ordenação**
- `menor_preco`: barato, baratinho, em conta, econômico, "tô liso".
- `avaliacao`: bem avaliado, o melhor, nota alta.
- `entrega_rapida`: só o superlativo ("o que chegar mais rápido"). Nesse caso `tempo_max_min` fica vazio.
- Se houver mais de um critério: `menor_preco` > `avaliacao` > `entrega_rapida`.

## Campo `alvo` (só no conjunto de referência)

Alguns pedidos citam um prato ou ingrediente que não cabe em nenhum campo ("algo com camarão"). Para avaliar a busca nesses casos, o caso traz `alvo`: uma lista de termos que precisam aparecer no nome ou na descrição do prato (sem acento, sem diferenciar maiúsculas). Itens da lista são combinados com E; alternativas dentro de um item são separadas por `|`.

Exemplo: `"alvo": ["moqueca", "camarao"]` exige os dois termos; `"alvo": ["sopa|caldo"]` aceita qualquer um.
