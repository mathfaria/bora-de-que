"""Busca de estabelecimentos reais perto do usuário.

Combina: filtros (categoria, distância), proximidade, busca textual (nome + cozinha + pratos
típicos) e similaridade vetorial, fundidos por RRF. Para cada lugar calcula:
- dieta: "confirmado" (o OSM informa) ou "costuma ter" (há pratos típicos compatíveis);
  lugares sem nenhum dos dois saem quando o pedido tem restrição alimentar;
- aberto agora / horário de hoje (quando o OSM tem horário);
- até 3 pratos sugeridos (típicos da cozinha ou regionais do estado) que respeitam restrições
  e itens a evitar.
O que não existe nos dados (preço, nota, tempo de entrega) não é inventado: volta como aviso.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from bocado.extraction.rules import normalizar
from bocado.extraction.schema import Filtros
from bocado.local.categorias import rotulo_categoria
from bocado.local.horario import aberto_agora, horario_de_hoje
from bocado.local.pratos import MINIMO_COSTUMA_TER, PratoRegional, PratoTipico, regionais, tipicos
from bocado.textos import STOPWORDS, sem_acento

TAG_EVITAR = {
    "fritura": "frito",
    "carne_vermelha": "carne_vermelha",
    "porco": "porco",
    "frutos_do_mar": "frutos_do_mar",
    "pimenta": "apimentado",
}
RAIO_PADRAO_KM = 3.0
RAIO_MAXIMO_KM = 8.0
RRF_K = 60


@dataclass
class Origem:
    lat: float
    lon: float
    uf: str
    fuso: str = "America/Sao_Paulo"


@dataclass
class ResultadoLugares:
    lugares: list[dict[str, Any]]
    filtros: Filtros
    raio_km: float
    relaxados: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    total_candidatos: int = 0


def palavras_chave(texto: str) -> list[str]:
    return [p for p in dict.fromkeys(re.findall(r"[a-z0-9]+", normalizar(texto))) if p not in STOPWORDS and len(p) > 2]


def _casa(prato_texto: str, termos: list[str]) -> int:
    t = sem_acento(prato_texto)
    return sum(1 for termo in termos if re.search(rf"(?<![a-z]){re.escape(termo[:6])}", t))


def status_dieta(restricao: str, dieta_osm: list[str], pratos: list[PratoTipico]) -> str | None:
    if restricao in dieta_osm or (restricao == "vegetariano" and "vegano" in dieta_osm):
        return "confirmado"
    if sum(1 for p in pratos if restricao in p.tags) >= MINIMO_COSTUMA_TER:
        return "costuma ter"
    return None


def combinacao_ok(dieta: dict[str, str | None], pratos: list[PratoTipico]) -> bool:
    """Restrições deduzidas pela cozinha precisam valer juntas, não só uma a uma.

    Um café tem pratos sem glúten e pratos sem lactose, mas nenhum típico que seja as duas coisas:
    para "sem glúten e sem lactose" ele não "costuma ter" nada. O que o OSM confirma não entra na conta.
    """
    inferidas = {r for r, v in dieta.items() if v == "costuma ter"}
    if len(inferidas) < 2:
        return True
    return sum(1 for p in pratos if inferidas <= set(p.tags)) >= MINIMO_COSTUMA_TER


def sugerir_pratos(
    lugar: dict[str, Any], filtros: Filtros, termos: list[str], regionais_uf: list[PratoRegional]
) -> list[dict[str, Any]]:
    # Se o pedido citou uma cozinha, os típicos dela vêm primeiro (uma cafeteria "japonesa" sugere sushi, não pão).
    pedidas = [c for c in lugar["categorias"] if c in filtros.categorias]
    cats = pedidas + [c for c in lugar["categorias"] if c not in pedidas]
    pratos = tipicos(cats, [] if pedidas else lugar["rotulos"], lugar["dieta"])
    proibidas = {TAG_EVITAR[e] for e in filtros.evitar}
    nome_lugar = sem_acento(lugar["nome"])
    candidatos: list[tuple[float, dict[str, Any]]] = []
    for p in pratos:
        if not set(filtros.restricoes) <= set(p.tags):  # pratos veganos já carregam a tag vegetariano
            continue
        if proibidas & set(p.tags):
            continue
        pontos = 3.0 * _casa(f"{p.nome} {p.descricao}", termos)
        pontos += sum(1.0 for a in filtros.atributos if a in p.tags)
        candidatos.append(
            (pontos, {"nome": p.nome, "descricao": p.descricao, "tags": list(p.tags), "origem": "típico"})
        )
    for r in regionais_uf:
        combina = set(r.onde) & set(lugar["categorias"]) or any(t in nome_lugar for t in r.termos)
        if not combina or filtros.restricoes or filtros.evitar:
            continue  # pratos regionais não têm tags de dieta confiáveis: só entram sem restrições
        pontos = (
            2.0
            + 3.0 * _casa(f"{r.nome} {r.descricao}", termos)
            + (2.0 if any(t in nome_lugar for t in r.termos) else 0.0)
        )
        candidatos.append((pontos, {"nome": r.nome, "descricao": r.descricao, "tags": [], "origem": "regional"}))
    candidatos.sort(key=lambda x: -x[0])
    vistos, out = set(), []
    for _, c in candidatos:
        if c["nome"] not in vistos:
            vistos.add(c["nome"])
            out.append(c)
        if len(out) == 3:
            break
    return out


def link_mapa(lugar: dict[str, Any]) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote(lugar['nome'])}%20{lugar['lat']:.5f}%2C{lugar['lon']:.5f}"


def link_osm(lugar_id: str) -> str:
    tipo = {"n": "node", "w": "way", "r": "relation"}[lugar_id.split(":")[1][0]]
    return f"https://www.openstreetmap.org/{tipo}/{lugar_id.split(':')[1][1:]}"


SQL = """
select l.id, l.nome, l.tipo, l.categorias, l.rotulos, l.origem_categoria, l.lat, l.lon, l.endereco, l.horario,
       l.site, l.telefone, l.dieta, l.entrega,
       round(haversine_km(%(lat)s, %(lon)s, l.lat, l.lon)::numeric, 2)::float as distancia_km,
       case when %(vetor)s::vector is null or l.embedding is null then null
            else 1 - (l.embedding <=> %(vetor)s::vector) end as similaridade,
       case when %(consulta)s::text is null then 0
            else ts_rank(l.busca, websearch_to_tsquery('portuguese', %(consulta)s::text)) end as rank_texto
from lugares l
where l.lat between %(lat)s - %(dlat)s and %(lat)s + %(dlat)s
  and l.lon between %(lon)s - %(dlon)s and %(lon)s + %(dlon)s
  and haversine_km(%(lat)s, %(lon)s, l.lat, l.lon) <= %(raio)s
  and (cardinality(%(categorias)s::text[]) = 0 or l.categorias && %(categorias)s::text[])
"""


def _candidatos(conn, origem: Origem, filtros: Filtros, raio: float, consulta: str | None, vetor) -> list[dict]:
    dlat = raio / 111.0
    dlon = raio / (111.0 * max(0.2, abs(math.cos(math.radians(origem.lat)))))
    return [
        dict(r)
        for r in conn.execute(
            SQL,
            {
                "lat": origem.lat,
                "lon": origem.lon,
                "dlat": dlat,
                "dlon": dlon,
                "raio": raio,
                "categorias": list(filtros.categorias),
                "consulta": consulta,
                "vetor": vetor,
            },
        ).fetchall()
    ]


def avisos_sem_dados(filtros: Filtros) -> list[str]:
    """O que o pedido cita e os dados não têm. Volta para o LLM dizer isso, em vez de fingir."""
    avisos: list[str] = []
    if filtros.preco_max or filtros.ordenar_por == "menor_preco":
        avisos.append("Não tenho preços reais; não diga que algo cabe no orçamento ou é barato. Explique isso.")
    if filtros.ordenar_por == "avaliacao":
        avisos.append("Não tenho notas reais; não diga que algo é bem avaliado. Explique isso.")
    if filtros.tempo_max_min or filtros.ordenar_por == "entrega_rapida":
        avisos.append("Não tenho o tempo de entrega real; no máximo, os lugares mais perto chegam antes.")
    return avisos


def buscar_lugares(
    conn,
    embedder,
    filtros: Filtros,
    texto: str,
    origem: Origem,
    limite: int = 5,
    so_abertos: bool = False,
    agora: datetime | None = None,
    modo: str = "hibrida",
) -> ResultadoLugares:
    from bocado.local.horario import agora_em

    agora = agora or agora_em(origem.fuso)
    termos = palavras_chave(texto)
    consulta = " or ".join(termos) if termos and modo in ("hibrida", "lexical") else None
    vetor = embedder.embed([texto])[0] if modo in ("hibrida", "vetorial") else None
    regionais_uf = regionais(origem.uf)
    avisos = avisos_sem_dados(filtros)

    raio = min(filtros.distancia_max_km or RAIO_PADRAO_KM, RAIO_MAXIMO_KM)
    relaxados: list[str] = []
    while True:
        brutos = _candidatos(conn, origem, filtros, raio, consulta, vetor)
        aprovados = []
        for lugar in brutos:
            pratos = tipicos(lugar["categorias"], lugar["rotulos"], lugar["dieta"])
            dieta = {r: status_dieta(r, lugar["dieta"], pratos) for r in filtros.restricoes}
            if any(v is None for v in dieta.values()) or not combinacao_ok(dieta, pratos):
                continue
            proibidas = {TAG_EVITAR[e] for e in filtros.evitar}
            if pratos and proibidas and all(proibidas & set(p.tags) for p in pratos):
                continue
            aberto = aberto_agora(lugar["horario"], agora)
            if so_abertos and aberto is False:
                continue
            lugar.update(dieta_status=dieta, aberto_agora=aberto, horario_hoje=horario_de_hoje(lugar["horario"], agora))
            aprovados.append(lugar)
        if len(aprovados) >= min(limite, 3) or raio >= RAIO_MAXIMO_KM:
            break
        raio = min(raio * 2, RAIO_MAXIMO_KM)
        relaxados.append("distancia")

    # Ranking: RRF entre proximidade, texto e vetor (+ pequenos bônus por informação confirmada).
    # O id desempata: sem ele, empates (filiais com o mesmo nome, mesmo prédio) saem na ordem física das linhas
    # no Postgres, que muda a cada regravação, e a mesma busca devolve lugares diferentes.
    ordens = [sorted(aprovados, key=lambda x: (x["distancia_km"], x["id"]))]
    if consulta:
        ordens.append(sorted([x for x in aprovados if x["rank_texto"] > 0], key=lambda x: (-x["rank_texto"], x["id"])))
    if vetor is not None:
        ordens.append(
            sorted([x for x in aprovados if x["similaridade"] is not None], key=lambda x: (-x["similaridade"], x["id"]))
        )
    pesos = [0.8] + [1.0] * (len(ordens) - 1)
    if filtros.tempo_max_min or filtros.ordenar_por == "entrega_rapida":
        pesos[0] = 2.0
    score: dict[str, float] = {}
    for peso, ordem in zip(pesos, ordens, strict=True):
        for pos, lugar in enumerate(ordem):
            score[lugar["id"]] = score.get(lugar["id"], 0.0) + peso / (RRF_K + pos + 1)
    for lugar in aprovados:
        bonus = 0.002 * sum(1 for v in lugar["dieta_status"].values() if v == "confirmado")
        bonus += 0.001 if lugar["aberto_agora"] else 0.0
        if not lugar["categorias"]:  # sem cozinha conhecida: não sabemos o que sugerir
            bonus -= 0.02 if consulta and lugar["rank_texto"] == 0 else 0.004
        score[lugar["id"]] += bonus
    aprovados.sort(key=lambda x: (-score[x["id"]], x["distancia_km"], x["id"]))

    escolhidos, nomes = [], set()
    for lugar in aprovados:
        chave = sem_acento(lugar["nome"])
        if chave in nomes:
            continue  # redes com várias unidades: fica a mais relevante/perto
        nomes.add(chave)
        lugar["sugestoes"] = sugerir_pratos(lugar, filtros, termos, regionais_uf)
        lugar["cozinha"] = ", ".join(lugar["rotulos"] or [rotulo_categoria(c) for c in lugar["categorias"]]) or None
        lugar["mapa_url"] = link_mapa(lugar)
        lugar["osm_url"] = link_osm(lugar["id"])
        lugar["score"] = round(score[lugar["id"]], 5)
        escolhidos.append(lugar)
        if len(escolhidos) == limite:
            break
    return ResultadoLugares(
        lugares=escolhidos,
        filtros=filtros,
        raio_km=raio,
        relaxados=relaxados,
        avisos=avisos,
        total_candidatos=len(aprovados),
    )


# Se nenhuma casa da cozinha do prato estiver mapeada por perto, onde mais costuma aparecer.
RELACIONADAS: dict[str, list[str]] = {
    "poke": ["japonesa", "saladas"],
    "tailandesa": ["chinesa", "japonesa"],
    "chinesa": ["japonesa", "tailandesa"],
    "japonesa": ["poke", "chinesa"],
    "mexicana": ["lanches", "hamburguer"],
    "acai": ["doces", "cafe"],
    "nordestina": ["brasileira"],
    "churrasco": ["brasileira"],
    "arabe": ["lanches"],
    "saladas": ["brasileira", "cafe"],
    "doces": ["cafe"],
    "cafe": ["doces", "lanches"],
    "italiana": ["pizza"],
    "pizza": ["italiana"],
    "hamburguer": ["lanches"],
    "lanches": ["hamburguer", "cafe"],
    "brasileira": ["nordestina"],
}


def onde_comer(
    conn,
    embedder,
    prato,
    origem: Origem,
    restricoes: list[str],
    evitar: list[str],
    limite: int = 5,
    agora: datetime | None = None,
) -> ResultadoLugares:
    """Lugares reais perto da pessoa onde o prato costuma existir (pela cozinha ou pelo nome da casa).

    Se nenhuma casa da cozinha do prato estiver mapeada por perto, tenta cozinhas relacionadas
    (poke -> japonesa), marcando a evidência como "pode ter".
    """
    restr = sorted(set(restricoes) | ({prato.exige_dieta} if prato.exige_dieta else set()))
    texto = f"{prato.nome} {' '.join(prato.termos)}"
    filtros = Filtros(categorias=prato.categorias_onde, restricoes=restr, evitar=sorted(set(evitar)))
    r = buscar_lugares(conn, embedder, filtros, texto, origem, limite=limite, agora=agora)
    relacionada = False
    if not r.lugares:
        outras = sorted(
            {c for cat in prato.categorias_onde for c in RELACIONADAS.get(cat, [])} - set(prato.categorias_onde)
        )
        if outras:
            filtros = filtros.model_copy(update={"categorias": outras})
            r = buscar_lugares(conn, embedder, filtros, texto, origem, limite=limite, agora=agora)
            relacionada = True
    termos_nome = [sem_acento(t) for t in prato.termos]
    for lugar in r.lugares:
        nome = sem_acento(lugar["nome"])
        if termos_nome and any(t in nome for t in termos_nome):
            lugar["evidencia"] = "o nome da casa indica que serve"
        elif relacionada:
            lugar["evidencia"] = f"pode ter: casas de {lugar['cozinha'] or 'comida parecida'} às vezes servem"
        else:
            lugar["evidencia"] = f"costuma ter em {lugar['cozinha'] or 'casas desse tipo'}"
    # Casas cujo nome indica o prato vêm primeiro (ex.: "Tapiocaria" para tapioca); depois, a mais perto.
    r.lugares.sort(key=lambda x: (x["evidencia"] != "o nome da casa indica que serve", x["distancia_km"], x["id"]))
    if relacionada:
        r.relaxados.append("cozinha")
    return r
