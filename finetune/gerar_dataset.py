"""Gera o dataset sintético de ajuste fino: pedido em português -> filtros JSON.

Por que gerado por templates e não por LLM: o rótulo nasce junto com o texto, então é correto
por construção e segue o guia (docs/guia-de-rotulagem.md) sem precisar de revisão item a item.
O custo é menos variedade de estilo; por isso o modelo é avaliado em pedidos escritos à mão,
que ele nunca viu (evals/cases), e não só no teste sintético.

    python finetune/gerar_dataset.py            -> finetune/data/{train,valid,test}.jsonl
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from pathlib import Path

from bocado.avaliacao import CASOS
from bocado.extraction import SISTEMA_MODELO_PEQUENO
from bocado.extraction.schema import Filtros

SEMENTE = 7
N_TREINO, N_VALID, N_TESTE = 2600, 200, 200
SAIDA = Path(__file__).resolve().parent / "data"

# --------------------------------------------------------------------- superfícies

CATEGORIA = {
    "japonesa": [
        "comida japonesa",
        "japonês",
        "japa",
        "sushi",
        "temaki",
        "sashimi",
        "um combinado de sushi",
        "ramen",
        "uramaki",
        "hot roll",
        "culinária japonesa",
    ],
    "chinesa": ["comida chinesa", "chinês", "yakisoba", "frango xadrez", "rolinho primavera", "chop suey"],
    "tailandesa": ["comida tailandesa", "tailandês", "thai", "pad thai", "tom yum"],
    "italiana": [
        "comida italiana",
        "italiano",
        "massa",
        "macarrão",
        "lasanha",
        "risoto",
        "nhoque",
        "espaguete",
        "penne",
        "ravioli",
        "uma cantina",
    ],
    "pizza": ["pizza", "uma pizza", "pizzaria", "pizza grande", "uma broto"],
    "hamburguer": ["hambúrguer", "hamburguer", "burger", "um lanche de hamburgueria", "smash burger"],
    "lanches": [
        "lanche",
        "sanduíche",
        "pastel",
        "coxinha",
        "salgado",
        "misto quente",
        "x-salada",
        "cachorro-quente",
        "um sanduba",
    ],
    "arabe": ["comida árabe", "árabe", "esfiha", "esfirra", "kibe", "quibe", "shawarma", "falafel", "kafta", "homus"],
    "mexicana": ["comida mexicana", "mexicano", "taco", "tacos", "burrito", "nachos", "quesadilla"],
    "brasileira": [
        "comida caseira",
        "comida brasileira",
        "prato feito",
        "PF",
        "marmita",
        "marmitex",
        "feijoada",
        "um executivo",
        "almoço caseiro",
        "strogonoff",
    ],
    "nordestina": [
        "comida nordestina",
        "nordestino",
        "baião de dois",
        "tapioca",
        "cuscuz",
        "moqueca",
        "acarajé",
        "carne de sol",
    ],
    "churrasco": ["churrasco", "picanha", "costela", "fraldinha", "espetinho", "carne na brasa", "churrascaria"],
    "saladas": ["salada", "uma saladinha", "salada completa", "saladas"],
    "poke": ["poke", "um poke", "poke bowl"],
    "acai": ["açaí", "um açaí", "açaizinho"],
    "doces": ["doce", "sobremesa", "um docinho", "brigadeiro", "bolo", "brownie", "pudim", "uma sobremesa"],
    "cafe": ["café da manhã", "padaria", "coisa de padaria", "padoca", "brunch"],
}

NUMERO_EXTENSO = {
    10: "dez",
    15: "quinze",
    20: "vinte",
    25: "vinte e cinco",
    30: "trinta",
    35: "trinta e cinco",
    40: "quarenta",
    45: "quarenta e cinco",
    50: "cinquenta",
    60: "sessenta",
    70: "setenta",
    80: "oitenta",
    100: "cem",
}

PRECO = [
    "até {n} reais",
    "até R$ {n}",
    "até R${n}",
    "no máximo {n} reais",
    "no máximo R$ {n}",
    "uns {n} reais",
    "uns {n} conto",
    "até {n} conto",
    "menos de {n} reais",
    "por até {n}",
    "com {n} reais",
    "até {n} pila",
    "gastando até {n}",
    "teto de {n} reais",
    "na faixa de {n} reais",
    "até {n}",
]

RESTRICAO = {
    "vegetariano": [
        "sou vegetariano",
        "sou vegetariana",
        "vegetariano",
        "vegetariana",
        "sem carne",
        "não como carne",
        "opção vegetariana",
        "nada de carne",
    ],
    "vegano": ["sou vegano", "sou vegana", "vegano", "vegana", "vegan", "opção vegana", "100% vegetal"],
    "sem_gluten": [
        "sem glúten",
        "sem gluten",
        "sou celíaco",
        "sou celíaca",
        "celíaco",
        "zero glúten",
        "não posso comer glúten",
    ],
    "sem_lactose": [
        "sem lactose",
        "sou intolerante a lactose",
        "intolerante à lactose",
        "sem leite",
        "zero lactose",
        "tenho intolerância a lactose",
    ],
}

EVITAR = {
    "fritura": [
        "sem fritura",
        "nada frito",
        "nada de fritura",
        "não quero nada empanado",
        "sem nada frito",
        "evitando fritura",
        "que não seja frito",
    ],
    "carne_vermelha": ["sem carne vermelha", "evito carne vermelha", "nada de carne de boi", "sem carne bovina"],
    "porco": ["sem porco", "não como porco", "sem bacon", "nada de carne de porco", "sem carne suína"],
    "frutos_do_mar": [
        "sem frutos do mar",
        "sem peixe",
        "tenho alergia a camarão",
        "nada de frutos do mar",
        "alérgico a frutos do mar",
    ],
    "pimenta": ["sem pimenta", "nada picante", "não gosto de pimenta", "nada apimentado", "sem ardência"],
}

ATRIBUTO = {
    "leve": [
        "leve",
        "levinho",
        "light",
        "saudável",
        "fit",
        "nada pesado",
        "tô de dieta",
        "algo mais leve",
        "bem saudável",
    ],
    "proteico": ["com bastante proteína", "proteico", "pós-treino", "rico em proteína", "com muita proteína"],
    "apimentado": ["apimentado", "picante", "bem ardido", "com pimenta", "bem apimentado"],
    "para_compartilhar": [
        "pra dois",
        "para duas pessoas",
        "pra dividir",
        "pra família",
        "pra galera",
        "pro casal",
        "pra 3 pessoas",
        "pra 4 pessoas",
        "para dividir com os amigos",
        "pra mim e minha esposa",
        "pra 5 pessoas",
        "pra um jantar a dois",
    ],
}

DIST_EXPLICITA = ["até {k} km", "num raio de {k} km", "no máximo {k} km daqui", "a menos de {k} km"]
DIST_VAGA = ["perto de casa", "aqui perto", "no bairro", "não muito longe", "pertinho", "aqui da região", "perto daqui"]
TEMPO_EXPLICITO = ["em até {m} minutos", "em {m} min", "que chegue em {m} minutos", "em no máximo {m} min"]
TEMPO_FIXO = [("em meia hora", 30), ("que chegue em meia hora", 30), ("em uma hora", 60), ("em até 1 hora", 60)]
TEMPO_VAGO = ["rápido", "tô com pressa", "urgente", "que não demore", "rapidinho", "com pressa", "que chegue logo"]

ORDENAR = {
    "menor_preco": ["barato", "baratinho", "em conta", "econômico", "tô liso", "o mais barato", "que seja barato"],
    "avaliacao": ["bem avaliado", "o melhor da região", "com nota alta", "mais bem avaliado", "que seja top"],
    "entrega_rapida": ["o que chegar mais rápido", "o que chega primeiro", "o mais rápido possível"],
}

# Ruído sem rótulo: ingredientes, pratos ambíguos, quantidades e contexto.
DISTRATORES = [
    "com camarão",
    "de frango",
    "com cogumelos",
    "com queijo",
    "com bastante molho",
    "pro almoço",
    "pra janta",
    "agora",
    "hoje",
    "quentinho",
    "caprichado",
    "bem gostoso",
    "com salmão",
    "de carne",
    "com batata frita",
    "de 500 ml",
    "com 20 peças",
    "pra hoje à noite",
    "curry",
    "pão de queijo",
    "com tofu",
    "de atum",
    "com abacate",
    "algo diferente",
    "que mate a fome",
]

ABERTURAS = [
    "quero",
    "queria",
    "me vê",
    "me indica",
    "tô a fim de",
    "bora pedir",
    "me sugere",
    "procuro",
    "preciso de",
    "pode ser",
    "manda",
    "me arruma",
    "tem",
    "vocês têm",
    "me recomenda",
    "gostaria de",
    "quero pedir",
    "",
]
ABERTURAS_GENERICAS = ["algo", "alguma coisa", "uma opção", "um prato", "comida", "uma refeição"]
CONECTORES = [", ", " e ", ", ", " ", ", mas ", "; ", ", e "]
FECHOS = ["", "", "", "?", " por favor", " pfv", " valeu", "!", " obrigado", " 🙏"]

VAZIOS = [
    "oi",
    "olá, tudo bem?",
    "o que você recomenda?",
    "me surpreenda",
    "tô com fome",
    "não sei o que pedir",
    "o que tem de bom hoje?",
    "me dá uma sugestão",
    "quero repetir meu último pedido",
    "boa noite!",
    "qual seu prato favorito?",
    "o que vocês têm aí?",
    "quero algo diferente hoje",
    "to morrendo de fome",
]


def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def ruido(t: str, rng: random.Random) -> str:
    r = rng.random()
    if r < 0.25:
        t = sem_acento(t)
    elif r < 0.30:
        t = t.upper()
    if rng.random() < 0.6:
        t = t.lower()
    trocas = [("você", "vc"), ("para", "pra"), ("que", "q"), ("tô", "to"), ("está", "tá"), ("por favor", "pfvr")]
    for a, b in trocas:
        if rng.random() < 0.3:
            t = re.sub(rf"\b{a}\b", b, t)
    if rng.random() < 0.05 and len(t) > 12:  # erro de digitação: troca duas letras vizinhas
        i = rng.randrange(1, len(t) - 2)
        if t[i].isalpha() and t[i + 1].isalpha():
            t = t[:i] + t[i + 1] + t[i] + t[i + 2 :]
    return t


def exemplo(rng: random.Random) -> tuple[str, Filtros]:
    if rng.random() < 0.06:
        return ruido(rng.choice(VAZIOS), rng), Filtros()

    rotulo: dict = {"categorias": [], "restricoes": [], "evitar": [], "atributos": []}
    frases: list[str] = []
    n_slots = rng.choices([1, 2, 3, 4, 5], weights=[22, 34, 26, 13, 5])[0]
    slots = rng.sample(
        ["categoria", "preco", "restricao", "evitar", "atributo", "distancia", "tempo", "ordenar"], k=n_slots
    )
    if "categoria" not in slots and rng.random() < 0.35:
        slots.append("categoria")

    cabeca = None
    for s in slots:
        if s == "categoria":
            cats = rng.sample(sorted(CATEGORIA), k=2 if rng.random() < 0.08 else 1)
            rotulo["categorias"] += cats
            termos = [rng.choice(CATEGORIA[c]) for c in cats]
            cabeca = " ou ".join(termos)
        elif s == "preco":
            n = rng.choice([15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 70, 80, 90, 100, 120, 150, 200])
            molde = rng.choice(PRECO)
            valor = (
                NUMERO_EXTENSO[n]
                if n in NUMERO_EXTENSO and "{n}" in molde and "R$" not in molde and rng.random() < 0.2
                else str(n)
            )
            rotulo["preco_max"] = n
            frases.append(molde.format(n=valor))
        elif s == "restricao":
            r = rng.choice(sorted(RESTRICAO))
            rotulo["restricoes"].append(r)
            frases.append(rng.choice(RESTRICAO[r]))
        elif s == "evitar":
            for e in rng.sample(sorted(EVITAR), k=2 if rng.random() < 0.15 else 1):
                rotulo["evitar"].append(e)
                frases.append(rng.choice(EVITAR[e]))
        elif s == "atributo":
            a = rng.choice(sorted(ATRIBUTO))
            if a == "apimentado" and "pimenta" in rotulo["evitar"]:
                continue
            rotulo["atributos"].append(a)
            frases.append(rng.choice(ATRIBUTO[a]))
        elif s == "distancia":
            if rng.random() < 0.45:
                k = rng.choice([1, 2, 3, 4, 5, 6, 8])
                rotulo["distancia_max_km"] = k
                frases.append(rng.choice(DIST_EXPLICITA).format(k=k))
            else:
                rotulo["distancia_max_km"] = 3
                frases.append(rng.choice(DIST_VAGA))
        elif s == "tempo":
            if rotulo.get("ordenar_por") == "entrega_rapida":
                continue
            r = rng.random()
            if r < 0.35:
                m = rng.choice([15, 20, 25, 30, 35, 40, 45, 50])
                rotulo["tempo_max_min"] = m
                frases.append(rng.choice(TEMPO_EXPLICITO).format(m=m))
            elif r < 0.5:
                txt, m = rng.choice(TEMPO_FIXO)
                rotulo["tempo_max_min"] = m
                frases.append(txt)
            else:
                rotulo["tempo_max_min"] = 30
                frases.append(rng.choice(TEMPO_VAGO))
        elif s == "ordenar":
            o = rng.choice(["menor_preco", "menor_preco", "avaliacao", "entrega_rapida"])
            if o == "entrega_rapida" and "tempo_max_min" in rotulo:
                continue
            rotulo["ordenar_por"] = o
            frases.append(rng.choice(ORDENAR[o]))

    if rng.random() < 0.35:
        frases.append(rng.choice(DISTRATORES))
    rng.shuffle(frases)

    abertura = rng.choice(ABERTURAS)
    if cabeca is None:
        cabeca = rng.choice(ABERTURAS_GENERICAS)
    partes = [p for p in [abertura, cabeca] if p]
    texto = " ".join(partes)
    for f in frases:
        texto += rng.choice(CONECTORES) + f
    texto = texto.strip(" ,;") + rng.choice(FECHOS)
    texto = texto[0].upper() + texto[1:] if rng.random() < 0.4 else texto
    return ruido(texto, rng), Filtros.model_validate(rotulo)


def registro(texto: str, f: Filtros) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SISTEMA_MODELO_PEQUENO},
            {"role": "user", "content": texto},
            {"role": "assistant", "content": f.json_compacto()},
        ]
    }


def main() -> None:
    rng = random.Random(SEMENTE)
    proibidos = {sem_acento(json.loads(x)["texto"].lower()) for x in CASOS.read_text().splitlines() if x.strip()}
    vistos: set[str] = set()
    exemplos = []
    while len(exemplos) < N_TREINO + N_VALID + N_TESTE:
        t, f = exemplo(rng)
        chave = sem_acento(t.lower())
        if chave in vistos or chave in proibidos:  # sem duplicatas e sem vazar o conjunto de referência
            continue
        vistos.add(chave)
        exemplos.append(registro(t, f))
    SAIDA.mkdir(parents=True, exist_ok=True)
    partes = {
        "train": exemplos[:N_TREINO],
        "valid": exemplos[N_TREINO : N_TREINO + N_VALID],
        "test": exemplos[N_TREINO + N_VALID :],
    }
    for nome, linhas in partes.items():
        with (SAIDA / f"{nome}.jsonl").open("w", encoding="utf-8") as fh:
            for linha in linhas:
                fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
    print({k: len(v) for k, v in partes.items()}, "->", SAIDA)
    for linha in exemplos[:12]:
        print(" ", linha["messages"][1]["content"], "=>", linha["messages"][2]["content"])


if __name__ == "__main__":
    main()
