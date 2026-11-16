"""Cozinhas do OpenStreetMap -> categorias do app (as 17 do esquema de filtros) + rótulo em português.

Metade dos lugares no OSM não informa `cuisine`. Nesses casos a categoria é deduzida do nome
("Pizzaria X", "Temakeria Y") ou do tipo do lugar (padaria, sorveteria). A origem fica registrada
para a interface poder dizer "deduzido pelo nome".
"""

from __future__ import annotations

import re

from bocado.textos import sem_acento

# valor de cuisine no OSM -> (categoria do app ou None, rótulo)
COZINHA: dict[str, tuple[str | None, str]] = {
    "regional": ("brasileira", "comida regional"),
    "brazilian": ("brasileira", "brasileira"),
    "homestyle": ("brasileira", "comida caseira"),
    "buffet": ("brasileira", "buffet"),
    "self_service": ("brasileira", "self-service"),
    "northeastern": ("nordestina", "nordestina"),
    "nordestina": ("nordestina", "nordestina"),
    "bahian": ("nordestina", "baiana"),
    "tapioca": ("nordestina", "tapiocaria"),
    "burger": ("hamburguer", "hamburgueria"),
    "pizza": ("pizza", "pizzaria"),
    "japanese": ("japonesa", "japonesa"),
    "sushi": ("japonesa", "sushi"),
    "ramen": ("japonesa", "lámen"),
    "temaki": ("japonesa", "temakeria"),
    "poke": ("poke", "poke"),
    "hawaiian": ("poke", "havaiana"),
    "italian": ("italiana", "italiana"),
    "pasta": ("italiana", "massas"),
    "chinese": ("chinesa", "chinesa"),
    "asian": ("chinesa", "asiática"),
    "thai": ("tailandesa", "tailandesa"),
    "vietnamese": ("tailandesa", "vietnamita"),
    "arab": ("arabe", "árabe"),
    "lebanese": ("arabe", "libanesa"),
    "syrian": ("arabe", "síria"),
    "turkish": ("arabe", "turca"),
    "kebab": ("arabe", "kebab"),
    "middle_eastern": ("arabe", "árabe"),
    "mexican": ("mexicana", "mexicana"),
    "tex-mex": ("mexicana", "tex-mex"),
    "steak_house": ("churrasco", "churrascaria"),
    "grill": ("churrasco", "grelhados"),
    "barbecue": ("churrasco", "churrasco"),
    "churrasco": ("churrasco", "churrascaria"),
    "argentinian": ("churrasco", "argentina"),
    "uruguayan": ("churrasco", "uruguaia"),
    "sandwich": ("lanches", "lanchonete"),
    "hot_dog": ("lanches", "cachorro-quente"),
    "pastel": ("lanches", "pastelaria"),
    "snack": ("lanches", "lanches"),
    "chicken": ("lanches", "frango"),
    "empanada": ("lanches", "empanadas"),
    "coffee_shop": ("cafe", "cafeteria"),
    "breakfast": ("cafe", "café da manhã"),
    "bakery": ("cafe", "padaria"),
    "cake": ("doces", "bolos"),
    "dessert": ("doces", "sobremesas"),
    "ice_cream": ("doces", "sorveteria"),
    "donut": ("doces", "donuts"),
    "chocolate": ("doces", "chocolates"),
    "crepe": ("doces", "crepes"),
    "brigadeiro": ("doces", "brigaderia"),
    "acai": ("acai", "açaí"),
    "juice": ("acai", "sucos"),
    "salad": ("saladas", "saladas"),
    "healthy": ("saladas", "saudável"),
    "vegetarian": ("saladas", "vegetariana"),
    "vegan": ("saladas", "vegana"),
    # Sem categoria correspondente no esquema: aparecem com rótulo próprio.
    "portuguese": (None, "portuguesa"),
    "peruvian": (None, "peruana"),
    "french": (None, "francesa"),
    "korean": (None, "coreana"),
    "indian": (None, "indiana"),
    "spanish": (None, "espanhola"),
    "greek": (None, "grega"),
    "seafood": (None, "frutos do mar"),
    "fish": (None, "peixes"),
    "international": (None, "internacional"),
    "fine_dining": (None, "alta gastronomia"),
}

# Palavras no nome que denunciam a cozinha (testadas sem acento, palavra inteira).
# A ordem importa: vence a primeira pista. Primeiro o que a casa VENDE ("Pastel do Japonês" é pastelaria),
# depois a origem ("Restaurante Japonês" é japonês) e por último palavras genéricas ("restaurante").
PISTAS_NOME: list[tuple[str, str]] = [
    (r"pizzaria|pizza", "pizza"),
    (r"temakeria|sushi|sashimi|temaki|yakisoba|lamen|ramen|nikkei", "japonesa"),
    (r"poke", "poke"),
    (r"hamburgueria|burger|burguer|smash", "hamburguer"),
    (r"churrascaria|churrasco|espetinho|parrilla|steakhouse|costelaria|costela|grill|galeto|galeteria", "churrasco"),
    (r"padaria|panificadora|padoca|cafeteria|cafe|coffee|confeitaria", "cafe"),
    (r"sorveteria|gelato|gelateria|doceria|brigaderia|bolos|chocolate|cupcake", "doces"),
    (r"acai|acaiteria|sucos|casa de sucos", "acai"),
    (r"pastelaria|pastel|lanchonete|lanches|sanduiche|hot dog|cachorro quente|salgados|coxinha", "lanches"),
    (r"esfiha|esfiharia|kebab|shawarma|falafel|kibe|quibe", "arabe"),
    (r"cantina|trattoria|massas|pasta", "italiana"),
    (r"taco|burrito|texmex", "mexicana"),
    (r"wok", "chinesa"),
    (r"tapiocaria|acaraje|cuscuz|baiao", "nordestina"),
    (r"salada|saladas|natural|fit|vegano|vegana|vegetariano", "saladas"),
    # origem
    (r"japones|japonesa|japa", "japonesa"),
    (r"libanes|libanesa|arabe|arabia|sirio", "arabe"),
    (r"italiano|italiana", "italiana"),
    (r"mexican|mexicano|mexicana", "mexicana"),
    (r"chines|chinesa|china|dragao", "chinesa"),
    (r"thai|tailandes|tailandesa", "tailandesa"),
    (r"nordestin|baiana|baiano|sertao", "nordestina"),
    # genéricas
    (
        r"restaurante|comida caseira|marmita|marmitex|self service|por quilo|kilo|buffet|cozinha|tropeiro|rotisserie|feijoada",
        "brasileira",
    ),
]

TIPO_PADRAO = {
    "cafe": "cafe",
    "ice_cream": "doces",
    "bakery": "cafe",
    "pastry": "doces",
    "fast_food": "lanches",
    "confectionery": "doces",
}


def classificar(tags: dict[str, str]) -> tuple[list[str], list[str], str]:
    """Devolve (categorias, rótulos, origem) para um lugar do OSM. origem: osm | nome | tipo | nenhuma."""
    categorias: list[str] = []
    rotulos: list[str] = []
    for valor in re.split(r"[;,]", tags.get("cuisine", "")):
        valor = valor.strip().lower().replace(" ", "_")
        if not valor:
            continue
        cat, rot = COZINHA.get(valor, (None, valor.replace("_", " ")))
        if cat and cat not in categorias:
            categorias.append(cat)
        if rot not in rotulos:
            rotulos.append(rot)
    if categorias:
        return categorias, rotulos, "osm"

    nome = sem_acento(tags.get("name", ""))
    for padrao, cat in PISTAS_NOME:
        if re.search(rf"(?<![a-z])(?:{padrao})(?![a-z])", nome):
            return [cat], rotulos or [rotulo_categoria(cat)], "nome"

    tipo = tags.get("amenity") or tags.get("shop") or ""
    if tipo in TIPO_PADRAO:
        cat = TIPO_PADRAO[tipo]
        return [cat], rotulos or [rotulo_categoria(cat)], "tipo"
    return [], rotulos, "nenhuma"


ROTULO_CATEGORIA = {
    "acai": "açaí",
    "arabe": "árabe",
    "brasileira": "brasileira",
    "cafe": "café e padaria",
    "chinesa": "chinesa",
    "churrasco": "churrasco",
    "doces": "doces",
    "hamburguer": "hamburgueria",
    "italiana": "italiana",
    "japonesa": "japonesa",
    "lanches": "lanches",
    "mexicana": "mexicana",
    "nordestina": "nordestina",
    "pizza": "pizzaria",
    "poke": "poke",
    "saladas": "saladas e saudável",
    "tailandesa": "tailandesa",
}


def rotulo_categoria(cat: str) -> str:
    return ROTULO_CATEGORIA.get(cat, cat)
