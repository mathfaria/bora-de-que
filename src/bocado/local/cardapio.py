"""Catálogo de pratos: o "o que comer" do app.

Junta os pratos típicos por cozinha (datagen/library.py) e os regionais do estado do usuário, cada
um com ID estável. A sugestão combina restrições (tags), similaridade semântica (embeddings dos
pratos, calculados uma vez e mantidos em memória), palavras do pedido e um destaque para os pratos
regionais. "Onde comer" é outra etapa: lugares reais cuja cozinha costuma servir o prato.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

import numpy as np

from bocado.datagen.library import POOLS
from bocado.extraction.schema import Filtros
from bocado.local.categorias import rotulo_categoria
from bocado.local.pratos import REGIONAIS
from bocado.textos import STOPWORDS, sem_acento

TAG_EVITAR = {
    "fritura": "frito",
    "carne_vermelha": "carne_vermelha",
    "porco": "porco",
    "frutos_do_mar": "frutos_do_mar",
    "pimenta": "apimentado",
}


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sem_acento(texto)).strip("-")


@dataclass(frozen=True)
class Prato:
    id: str
    nome: str
    descricao: str
    categoria: str  # cozinha principal (categoria do app)
    tags: tuple[str, ...] = ()
    regional_uf: str | None = None
    onde: tuple[str, ...] = ()  # categorias de casa onde procurar (regionais)
    termos: tuple[str, ...] = ()  # palavras no nome do lugar que indicam o prato (regionais)
    exige_dieta: str | None = None  # pratos do cardápio vegano: só em casas veganas

    @property
    def categorias_onde(self) -> list[str]:
        return list(self.onde) if self.onde else [self.categoria]

    def publico(self) -> dict:
        return {
            "id": self.id,
            "nome": self.nome,
            "descricao": self.descricao,
            "categoria": self.categoria,
            "cozinha": rotulo_categoria(self.categoria),
            "tags": list(self.tags),
            "regional": self.regional_uf,
            "exige_dieta": self.exige_dieta,
        }


def _montar() -> list[Prato]:
    pratos: dict[str, Prato] = {}
    for chave, (cat, itens) in POOLS.items():
        for nome, desc, _preco, _kcal, tags, _serve in itens:
            base = nome.split(" (")[0]
            pid = f"{cat}:{slug(base)}"
            if pid in pratos:
                continue
            pratos[pid] = Prato(
                pid,
                base,
                desc,
                cat,
                tuple(sorted(tags.split())),
                exige_dieta="vegano" if chave == "brasileira_vegana" else None,
            )
    for uf, itens in REGIONAIS.items():
        for r in itens:
            pid = f"regional-{uf.lower()}:{slug(r.nome)}"
            pratos[pid] = Prato(pid, r.nome, r.descricao, r.onde[0], (), regional_uf=uf, onde=r.onde, termos=r.termos)
    return list(pratos.values())


CATALOGO: list[Prato] = _montar()
POR_ID: dict[str, Prato] = {p.id: p for p in CATALOGO}


@dataclass
class _Indice:
    vetores: np.ndarray | None = None
    nome_embedder: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_indice = _Indice()


def _vetores(embedder) -> np.ndarray:
    with _indice.lock:
        if _indice.vetores is None or _indice.nome_embedder != embedder.nome:
            textos = [f"{p.nome}. {p.descricao}. Comida {rotulo_categoria(p.categoria)}." for p in CATALOGO]
            _indice.vetores = embedder.embed(textos)
            _indice.nome_embedder = embedder.nome
        return _indice.vetores


def _termos(texto: str) -> list[str]:
    return [t for t in dict.fromkeys(re.findall(r"[a-z0-9]+", sem_acento(texto))) if t not in STOPWORDS and len(t) > 2]


def compativel(p: Prato, filtros: Filtros, uf: str | None) -> bool:
    if p.regional_uf and p.regional_uf != uf:
        return False
    if filtros.restricoes and not set(filtros.restricoes) <= set(p.tags):
        return False  # regionais não têm tags de dieta confiáveis: saem quando há restrição
    if p.regional_uf and (filtros.atributos or filtros.evitar):
        return False  # nem quando pedem leve/proteico/apimentado ou algo a evitar: não dá para garantir
    if any(TAG_EVITAR[e] in p.tags for e in filtros.evitar):
        return False
    if any(a not in p.tags for a in filtros.atributos if a in ("leve", "proteico", "apimentado")):
        return False  # nos pratos as tags existem: "algo leve" só traz prato leve
    if filtros.categorias and not set(p.categorias_onde) & set(filtros.categorias):
        return False
    if p.exige_dieta and p.exige_dieta not in filtros.restricoes:
        return False  # pratos de casa vegana só quando a pessoa pediu vegano
    return True


def sugerir(embedder, filtros: Filtros, texto: str, uf: str | None, limite: int = 4) -> list[dict]:
    vetores = _vetores(embedder)
    q = embedder.embed([texto])[0]
    sims = vetores @ q
    termos = _termos(texto)
    candidatos = []
    for i, p in enumerate(CATALOGO):
        if not compativel(p, filtros, uf):
            continue
        alvo = sem_acento(f"{p.nome} {p.descricao}")
        lexico = sum(1 for t in termos if re.search(rf"(?<![a-z]){re.escape(t[:6])}", alvo))
        score = float(sims[i]) + 0.35 * lexico
        score += 0.12 * sum(1 for a in filtros.atributos if a in p.tags)
        if p.regional_uf:
            score += 0.15  # o que é típico daqui ganha destaque
        candidatos.append((score, p))
    candidatos.sort(key=lambda x: -x[0])
    escolhidos: list[dict] = []
    por_cozinha: dict[str, int] = {}
    for score, p in candidatos:
        if por_cozinha.get(p.categoria, 0) >= 2 and not filtros.categorias:
            continue  # variedade: no máximo 2 pratos da mesma cozinha
        por_cozinha[p.categoria] = por_cozinha.get(p.categoria, 0) + 1
        escolhidos.append(p.publico() | {"score": round(score, 4)})
        if len(escolhidos) == limite:
            break
    return escolhidos


def regionais_do_estado(uf: str) -> list[dict]:
    return [p.publico() for p in CATALOGO if p.regional_uf == uf]
