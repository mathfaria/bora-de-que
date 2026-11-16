"""Monta as tabelas de resultados a partir de evals/results/*.json e atualiza o README.

Nenhum número do README é digitado à mão: tudo sai daqui.

    python evals/relatorio.py
"""

from __future__ import annotations

import json
import re

from bocado.avaliacao import RESULTADOS
from bocado.config import RAIZ

README = RAIZ / "README.md"
INICIO, FIM = "<!-- resultados:inicio -->", "<!-- resultados:fim -->"


def ler(nome: str) -> dict | None:
    arq = RESULTADOS / f"{nome}.json"
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%".replace(".", ",")


def ms(v: float) -> str:
    return f"{v:,.0f} ms".replace(",", ".")


ROTULO_EXTRATOR = {
    "rules": "Regras (baseline)",
    "base": "Qwen2.5-0.5B sem ajuste (prompt com o guia)",
    "finetuned": "Qwen2.5-0.5B + LoRA (local)",
    "llm": "Gemini via API",
}


def tabela_extracao(casos: str) -> list[str]:
    linhas = [
        "| Extrator | Acerto exato | Média por campo | JSON válido | Latência p50 | Latência p95 | Custo / 1.000 pedidos |",
        "|---|---|---|---|---|---|---|",
    ]
    achou = False
    for ext in ("rules", "base", "finetuned", "llm"):
        d = ler(f"extracao_{ext}_{casos}")
        if not d:
            continue
        achou = True
        m = d["metricas"]
        nome = ROTULO_EXTRATOR[ext]
        if ext == "llm" and d["meta"].get("modelo"):
            nome = f"{d['meta']['modelo']} via API"
        custo = f"US$ {m['custo_usd_por_1000']:.2f}".replace(".", ",") if ext == "llm" else "US$ 0 (CPU própria)"
        linhas.append(
            f"| {nome} | {pct(m['acerto_exato'])} | {pct(m['acerto_medio_campos'])} | "
            f"{pct(m['json_valido'])} | {ms(m['latencia_ms']['p50'])} | {ms(m['latencia_ms']['p95'])} | {custo} |"
        )
    return linhas if achou else []


def tabela_campos(casos: str) -> list[str]:
    exts = [e for e in ("rules", "base", "finetuned", "llm") if ler(f"extracao_{e}_{casos}")]
    if not exts:
        return []
    cab = "| Campo | " + " | ".join(ROTULO_EXTRATOR[e] for e in exts) + " |"
    linhas = [cab, "|---" * (len(exts) + 1) + "|"]
    campos = ler(f"extracao_{exts[0]}_{casos}")["metricas"]["acerto_por_campo"].keys()
    for c in campos:
        vals = [pct(ler(f"extracao_{e}_{casos}")["metricas"]["acerto_por_campo"][c]) for e in exts]
        linhas.append(f"| `{c}` | " + " | ".join(vals) + " |")
    return linhas


def tabela_busca(casos: str) -> list[str]:
    linhas = [
        "| Filtros | Categoria certa | Restrições respeitadas | Item a evitar respeitado | Prato citado aparece | Sem resultado |",
        "|---|---|---|---|---|---|",
    ]
    achou = False
    for filtros, rotulo in (
        ("gold", "gabarito"),
        ("rules", "regras"),
        ("finetuned", "modelo ajustado"),
        ("llm", "Gemini"),
    ):
        d = ler(f"busca_{filtros}_{casos}")
        if not d:
            continue
        achou = True
        m = d["metricas"]
        linhas.append(
            f"| {rotulo} | {pct(m['categoria_ok@3'])} | {pct(m['restricoes_ok@3'])} | {pct(m['evitar_ok@3'])} | "
            f"{pct(m['alvo@3'])} | {pct(m['sem_resultado'])} |"
        )
    if achou:
        linhas += [
            "",
            "Top-3 de 400 buscas (100 pedidos × 4 capitais), sobre recortes reais e congelados do OpenStreetMap.",
        ]
    return linhas if achou else []


def tabela_agente(casos: str) -> list[str]:
    d = ler(f"agente_{casos}")
    if not d:
        return []
    m = d["metricas"]
    nota = "—" if m["nota_juiz_media"] is None else f"{m['nota_juiz_media']:.2f}".replace(".", ",")
    return [
        f"Rodado com `{d['meta']['modelo']}` em {m['n_modo_agente']} pedidos, cada um numa das 4 capitais.",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Respostas com ID inventado na 1ª tentativa (antes da trava) | {pct(m['respostas_com_id_inventado_1a_tentativa'])} |",
        f"| IDs inventados entregues ao usuário (depois da trava) | {m['ids_inventados_entregues']} |",
        f'| Seguiu o fluxo "pratos primeiro" | {pct(m["seguiu_pratos_primeiro"])} |',
        f"| Pratos que respeitam restrições, cozinha e vontades do pedido | {pct(m['pratos_que_respeitam_o_pedido'])} |",
        f"| Respostas que citam preço (dado que não temos) | {pct(m['respostas_que_citam_preco'])} |",
        f"| Respostas sem nenhuma sugestão | {pct(m['respostas_sem_sugestao'])} |",
        f"| Nota média do juiz (1 a 5) | {nota} |",
        f"| Latência p50 / p95 | {ms(m['latencia_ms']['p50'])} / {ms(m['latencia_ms']['p95'])} |",
        f"| Tokens por conversa | {m['tokens_por_conversa']:,.0f} |".replace(",", "."),
        f"| Custo por conversa (preço de tabela) | US$ {m['custo_usd_por_conversa']:.4f} |",
    ]


def montar() -> str:
    partes = []
    for titulo, linhas in (
        ("Extração de filtros (100 pedidos escritos à mão)", tabela_extracao("pedidos")),
        ("Acerto por campo", tabela_campos("pedidos")),
        ("Extração no conjunto desafio", tabela_extracao("desafio")),
        ("Busca em lugares reais", tabela_busca("pedidos")),
        ("Agente de ponta a ponta", tabela_agente("pedidos")),
    ):
        if linhas:
            partes.append(f"#### {titulo}\n\n" + "\n".join(linhas))
    return "\n\n".join(partes) if partes else "_Ainda sem resultados: rode os scripts em `evals/`._"


def main() -> None:
    bloco = montar()
    (RESULTADOS / "RESULTADOS.md").write_text("# Resultados\n\n" + bloco + "\n", encoding="utf-8")
    texto = README.read_text(encoding="utf-8")
    if INICIO in texto and FIM in texto:
        novo = re.sub(re.escape(INICIO) + r".*?" + re.escape(FIM), f"{INICIO}\n{bloco}\n{FIM}", texto, flags=re.S)
        README.write_text(novo, encoding="utf-8")
        print("README atualizado")
    print(bloco)


if __name__ == "__main__":
    main()
