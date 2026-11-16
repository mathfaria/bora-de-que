# Resultados

#### Extração de filtros (100 pedidos escritos à mão)

| Extrator | Acerto exato | Média por campo | JSON válido | Latência p50 | Latência p95 | Custo / 1.000 pedidos |
|---|---|---|---|---|---|---|
| Regras (baseline) | 90,0% | 98,8% | 100,0% | 0 ms | 1 ms | US$ 0 (CPU própria) |
| Qwen2.5-0.5B sem ajuste (prompt com o guia) | 10,0% | 80,9% | 1,0% | 2.941 ms | 3.778 ms | US$ 0 (CPU própria) |
| Qwen2.5-0.5B + LoRA (local) | 58,0% | 92,9% | 98,0% | 517 ms | 831 ms | US$ 0 (CPU própria) |
| gemini-3.5-flash-lite via API | 93,0% | 99,1% | 100,0% | 971 ms | 1.490 ms | US$ 0,37 |

#### Acerto por campo

| Campo | Regras (baseline) | Qwen2.5-0.5B sem ajuste (prompt com o guia) | Qwen2.5-0.5B + LoRA (local) | Gemini via API |
|---|---|---|---|---|
| `categorias` | 95,0% | 33,0% | 76,0% | 97,0% |
| `preco_max` | 100,0% | 89,0% | 99,0% | 100,0% |
| `restricoes` | 100,0% | 80,0% | 94,0% | 99,0% |
| `evitar` | 99,0% | 91,0% | 94,0% | 100,0% |
| `atributos` | 98,0% | 79,0% | 91,0% | 98,0% |
| `distancia_max_km` | 98,0% | 95,0% | 99,0% | 100,0% |
| `tempo_max_min` | 100,0% | 93,0% | 99,0% | 100,0% |
| `ordenar_por` | 100,0% | 87,0% | 91,0% | 99,0% |

#### Extração no conjunto desafio

| Extrator | Acerto exato | Média por campo | JSON válido | Latência p50 | Latência p95 | Custo / 1.000 pedidos |
|---|---|---|---|---|---|---|
| Regras (baseline) | 66,2% | 95,2% | 100,0% | 1 ms | 2 ms | US$ 0 (CPU própria) |
| Qwen2.5-0.5B + LoRA (local) | 25,0% | 85,3% | 97,5% | 578 ms | 1.049 ms | US$ 0 (CPU própria) |
| gemini-3.5-flash-lite via API | 86,2% | 97,3% | 100,0% | 944 ms | 1.732 ms | US$ 0,36 |

#### Busca em lugares reais

| Filtros | Categoria certa | Restrições respeitadas | Item a evitar respeitado | Prato citado aparece | Sem resultado |
|---|---|---|---|---|---|
| gabarito | 100,0% | 100,0% | 100,0% | 89,6% | 4,5% |
| regras | 95,3% | 100,0% | 100,0% | 87,5% | 4,5% |
| modelo ajustado | 93,4% | 70,5% | 76,5% | 79,2% | 4,8% |
| Gemini | 98,5% | 100,0% | 100,0% | 92,7% | 4,5% |

Top-3 de 400 buscas (100 pedidos × 4 capitais), sobre recortes reais e congelados do OpenStreetMap.
