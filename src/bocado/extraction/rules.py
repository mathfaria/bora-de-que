"""Extrator de linha de base: regras e léxico, sem modelo.

Serve de baseline nas avaliações e de reserva quando o modelo não está disponível.
É rápido e previsível, mas não entende paráfrase nem contexto.
"""

from __future__ import annotations

import re
import unicodedata

from bocado.extraction.schema import Filtros


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("r$", " r$ ")
    t = re.sub(r"[^a-z0-9$,.;!?\- ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


CATEGORIA_TERMOS: dict[str, list[str]] = {
    "japonesa": [
        "japonesa",
        "japones",
        "japa",
        "sushi",
        "sashimi",
        "temaki",
        "uramaki",
        "hot roll",
        "ramen",
        "lamen",
        "niguiri",
        "combinado",
    ],
    "chinesa": ["chinesa", "chines", "yakisoba", "frango xadrez", "rolinho primavera", "chop suey"],
    "tailandesa": ["tailandesa", "tailandes", "thai", "pad thai", "tom yum"],
    "italiana": [
        "italiana",
        "italiano",
        "massa",
        "massas",
        "macarrao",
        "espaguete",
        "spaghetti",
        "lasanha",
        "nhoque",
        "risoto",
        "penne",
        "fettuccine",
        "ravioli",
        "cantina",
        "carbonara",
    ],
    "pizza": ["pizza", "pizzas", "pizzaria"],
    "hamburguer": ["hamburguer", "hamburger", "burger", "burguer", "burgers", "smash", "hamburgueria"],
    "lanches": [
        "lanche",
        "lanches",
        "sanduiche",
        "sanduba",
        "pastel",
        "coxinha",
        "salgado",
        "salgados",
        "misto quente",
        "x-salada",
        "x salada",
        "cachorro-quente",
        "cachorro quente",
        "hot dog",
    ],
    "arabe": [
        "arabe",
        "esfiha",
        "esfiha",
        "esfirra",
        "esfihas",
        "kibe",
        "quibe",
        "shawarma",
        "falafel",
        "homus",
        "kafta",
        "tabule",
    ],
    "mexicana": ["mexicana", "mexicano", "taco", "tacos", "burrito", "nachos", "quesadilla", "enchilada"],
    "brasileira": [
        "comida brasileira",
        "comida caseira",
        "caseira",
        "caseirinha",
        "prato feito",
        "pf",
        "marmita",
        "marmitex",
        "executivo",
        "feijoada",
        "strogonoff",
        "estrogonofe",
    ],
    "nordestina": ["nordestina", "nordestino", "baiao", "tapioca", "cuscuz", "acaraje", "moqueca", "carne de sol"],
    "churrasco": [
        "churrasco",
        "churrascaria",
        "picanha",
        "costela",
        "fraldinha",
        "espetinho",
        "carne na brasa",
        "parrilla",
    ],
    "saladas": ["salada", "saladas"],
    "poke": ["poke", "pokes"],
    "acai": ["acai"],
    "doces": ["doce", "doces", "docinho", "sobremesa", "brigadeiro", "bolo", "brownie", "pudim"],
    "cafe": ["cafe da manha", "padaria", "padoca", "brunch"],
}

NEGACOES = r"(?:sem|nada de|nada|zero|evit\w*|alergi\w* (?:a|ao|de)|nao (?:quero|gosto de|como|curto|posso|pode ter|tolero|suporto|como nada de)|menos|livre de|que nao (?:seja|tenha|leve))"

EVITAR_TERMOS: dict[str, list[str]] = {
    "fritura": ["fritura", "frituras", "frito", "fritos", "frita", "fritas", "empanado", "oleoso", "gordura"],
    "carne_vermelha": ["carne vermelha", "carne de boi", "carne bovina"],
    "porco": ["porco", "carne de porco", "carne suina", "suino", "bacon", "linguica", "calabresa"],
    "frutos_do_mar": [
        "frutos do mar",
        "fruto do mar",
        "peixe",
        "peixes",
        "camarao",
        "mariscos",
        "marisco",
        "frutos-do-mar",
    ],
    "pimenta": ["pimenta", "apimentado", "apimentada", "picante", "ardido", "ardida"],
}

RESTRICAO_TERMOS: dict[str, list[str]] = {
    "vegano": ["vegano", "vegana", "veganos", "veganas", "vegan", "vgn", "plant based", "plant-based"],
    "vegetariano": ["vegetariano", "vegetariana", "vegetarianos", "vegetarianas", "veggie", "vegetariano"],
    "sem_gluten": [
        "sem gluten",
        "celiaco",
        "celiaca",
        "gluten free",
        "zero gluten",
        "intolerante a gluten",
        "intolerancia a gluten",
    ],
    "sem_lactose": [
        "sem lactose",
        "zero lactose",
        "intolerante a lactose",
        "intolerancia a lactose",
        "sem leite",
        "lactose free",
    ],
}

ATRIBUTO_TERMOS: dict[str, list[str]] = {
    "leve": [
        "leve",
        "levinho",
        "levinha",
        "light",
        "saudavel",
        "saudaveis",
        "fit",
        "nada pesado",
        "nao muito pesado",
        "que nao pese",
        "sem pesar",
    ],
    "proteico": ["proteina", "proteinas", "proteico", "proteica", "pos-treino", "pos treino", "hipertrofia"],
    "apimentado": ["apimentado", "apimentada", "picante", "ardido", "ardida", "spicy", "com pimenta"],
    "para_compartilhar": [
        "para dois",
        "pra dois",
        "pra 2",
        "para 2",
        "para duas pessoas",
        "pra duas pessoas",
        "pra dividir",
        "para dividir",
        "compartilhar",
        "pra familia",
        "para a familia",
        "para familia",
        "pra galera",
        "para a galera",
        "pro casal",
        "para o casal",
        "para tres",
        "pra tres",
        "pra 3",
        "para 3",
        "pra 4",
        "para 4",
    ],
}

PALAVRAS_NUMERO = {
    "dez": 10,
    "quinze": 15,
    "vinte": 20,
    "vinte e cinco": 25,
    "trinta": 30,
    "trinta e cinco": 35,
    "quarenta": 40,
    "quarenta e cinco": 45,
    "cinquenta": 50,
    "sessenta": 60,
    "setenta": 70,
    "oitenta": 80,
    "noventa": 90,
    "cem": 100,
    "um": 1,
    "dois": 2,
    "tres": 3,
    "cinco": 5,
}

BARATO = [
    "barato",
    "barata",
    "baratinho",
    "baratinha",
    "economico",
    "economica",
    "em conta",
    "mais barato",
    "gastar pouco",
    "sem gastar muito",
    "pouco dinheiro",
    "to liso",
    "estou liso",
    "to duro",
    "precinho",
]
AVALIACAO = [
    "bem avaliado",
    "bem avaliada",
    "melhor avaliado",
    "melhor avaliada",
    "mais bem avaliado",
    "nota alta",
    "os melhores",
    "o melhor",
    "bem recomendado",
    "avaliacao boa",
    "boa avaliacao",
]
MAIS_RAPIDO = [
    "o mais rapido",
    "a mais rapida",
    "o que chegar mais rapido",
    "o que chega mais rapido",
    "chegue primeiro",
    "chega primeiro",
    "mais rapido possivel",
]
RAPIDO = ["rapido", "rapida", "rapidinho", "pressa", "urgente", "logo", "correndo", "sem demorar", "nao demore"]
PERTO = [
    "perto",
    "pertinho",
    "proximo",
    "proxima",
    "aqui do lado",
    "na minha regiao",
    "nao muito longe",
    "do bairro",
    "aqui perto",
]


def _tem(texto: str, termos: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", texto) for t in termos)


def _negado(texto: str, termo: str) -> bool:
    """O termo aparece logo depois de uma negação, na mesma oração (até 3 palavras entre)."""
    padrao = rf"{NEGACOES}\s+(?:[a-z]+\s+){{0,3}}?{re.escape(termo)}(?![a-z])"
    for oracao in re.split(r"[,.;!?]| mas | porem ", texto):
        if re.search(padrao, oracao):
            return True
    return False


def _numero(token: str) -> float | None:
    token = token.strip().replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return PALAVRAS_NUMERO.get(token)


NUM = r"(\d+(?:[.,]\d+)?|vinte e cinco|trinta e cinco|quarenta e cinco|dez|quinze|vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|cem)"


def _extrair_tempo(t: str) -> tuple[int | None, str]:
    m = re.search(rf"{NUM}\s*(?:min\b|mins\b|minutos?)", t)
    if m and (v := _numero(m.group(1))):
        return int(v), t.replace(m.group(0), " ")
    if re.search(r"meia hora|meia horinha", t):
        return 30, t
    m = re.search(r"(?:uma|1)\s*(?:hora|h\b)", t)
    if m:
        return 60, t.replace(m.group(0), " ")
    return None, t


def _extrair_distancia(t: str) -> tuple[float | None, str]:
    m = re.search(rf"{NUM}\s*(?:km|quilometros?|kms)\b", t)
    if m and (v := _numero(m.group(1))):
        return float(v), t.replace(m.group(0), " ")
    return None, t


def _extrair_preco(t: str) -> float | None:
    padroes = [
        rf"r\$\s*{NUM}",
        rf"{NUM}\s*(?:reais|real|conto|contos|pila|pilas|mangos|dinheiros)\b",
        rf"(?:ate|no maximo|maximo|menos de|abaixo de|uns|em torno de|na faixa de|por volta de|teto de)\s+(?:uns\s+)?{NUM}(?!\s*(?:pessoas|un|unidades|pecas|fatias))",
    ]
    for p in padroes:
        m = re.search(p, t)
        if m and (v := _numero(m.group(1))) and v >= 8:
            return float(v)
    return None


def extrair(texto: str) -> Filtros:
    t = normalizar(texto)
    tempo, t_sem_tempo = _extrair_tempo(t)
    dist, t_sem_num = _extrair_distancia(t_sem_tempo)

    evitar = set()
    for valor, termos in EVITAR_TERMOS.items():
        if any(_negado(t, termo) for termo in termos):
            evitar.add(valor)

    restricoes = set()
    for valor, termos in RESTRICAO_TERMOS.items():
        if _tem(t, termos):
            restricoes.add(valor)
    if re.search(r"(?:sem|nada de|nao como|nao quero|zero) carne(?! vermelha| de boi| bovina| de porco| suina)", t):
        restricoes.add("vegetariano")
    if re.search(rf"{NEGACOES}\s+(?:[a-z]+\s+){{0,2}}?(?:lactose|leite|laticinios?)", t):
        restricoes.add("sem_lactose")
    if re.search(rf"{NEGACOES}\s+(?:[a-z]+\s+){{0,2}}?gluten", t):
        restricoes.add("sem_gluten")

    atributos = set()
    for valor, termos in ATRIBUTO_TERMOS.items():
        for termo in termos:
            if _tem(t, [termo]) and not (valor == "apimentado" and _negado(t, termo)):
                atributos.add(valor)
    if "pimenta" in evitar:
        atributos.discard("apimentado")
    if re.search(rf"{NEGACOES}\s+(?:[a-z]+\s+){{0,2}}?pesad[oa]", t):
        atributos.add("leve")

    categorias = set()
    t_cat = re.sub(r"x-? ?salada", " lanche ", t)
    for valor, termos in CATEGORIA_TERMOS.items():
        if any(_tem(t_cat, [termo]) and not _negado(t_cat, termo) for termo in termos):
            categorias.add(valor)

    if dist is None and _tem(t, PERTO):
        dist = 3.0

    ordenar = "relevancia"
    if _tem(t, BARATO):
        ordenar = "menor_preco"
    elif _tem(t, AVALIACAO):
        ordenar = "avaliacao"
    elif _tem(t, MAIS_RAPIDO):
        ordenar = "entrega_rapida"
    if tempo is None and ordenar != "entrega_rapida" and _tem(t, RAPIDO):
        tempo = 30

    return Filtros(
        categorias=sorted(categorias),
        preco_max=_extrair_preco(t_sem_num),
        restricoes=sorted(restricoes),
        evitar=sorted(evitar),
        atributos=sorted(atributos),
        distancia_max_km=dist,
        tempo_max_min=tempo,
        ordenar_por=ordenar,
    )
