"""Estabelecimentos reais do OpenStreetMap via Overpass API, com cache por região no Postgres.

Fluxo: garantir_area(lat, lon) -> se a região já foi baixada nos últimos 30 dias, não faz nada;
senão consulta o Overpass (2,5 km; 6 km se a cidade for pequena), classifica e grava os lugares.
Os embeddings são calculados numa thread separada: a busca textual e por filtros já funciona
enquanto isso, e a vetorial entra quando terminar.

Dados © colaboradores do OpenStreetMap, licença ODbL (a interface mostra a atribuição).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from bocado.local.categorias import classificar, rotulo_categoria
from bocado.local.cep import user_agent
from bocado.local.pratos import tipicos
from bocado.textos import sem_acento

log = logging.getLogger(__name__)

ESPELHOS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
RAIO_PADRAO_M = 2500
RAIO_CIDADE_PEQUENA_M = 6000
MINIMO_LUGARES = 40
COBERTURA_KM = 1.5  # um ponto conta como coberto se estiver a até 1,5 km do centro de uma área baixada
VALIDADE_DIAS = 30

CONSULTA = """[out:json][timeout:60];
(
  nwr["amenity"~"^(restaurant|fast_food|cafe|ice_cream|food_court)$"]["name"](around:{raio},{lat},{lon});
  nwr["shop"~"^(bakery|pastry|confectionery)$"]["name"](around:{raio},{lat},{lon});
);
out center tags;"""


class OverpassIndisponivel(RuntimeError):
    pass


def _post(url: str, consulta: str) -> list[dict[str, Any]]:
    with httpx.Client(timeout=httpx.Timeout(75.0, connect=10.0), headers={"User-Agent": user_agent()}) as c:
        r = c.post(url, data={"data": consulta})
    if r.status_code != 200:
        raise OverpassIndisponivel(f"{url.split('/')[2]}: {r.status_code}")
    return r.json().get("elements", [])


def consultar_overpass(lat: float, lon: float, raio_m: int, atraso_reserva_s: float = 20.0) -> list[dict[str, Any]]:
    """Consulta o servidor principal; se ele não responder em `atraso_reserva_s`, dispara um espelho em
    paralelo e fica com a primeira resposta boa (requisição de reserva, sem martelar os três de uma vez)."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    consulta = CONSULTA.format(raio=raio_m, lat=f"{lat:.6f}", lon=f"{lon:.6f}")
    erros: list[str] = []
    ex = ThreadPoolExecutor(max_workers=len(ESPELHOS))
    try:
        pendentes = {ex.submit(_post, ESPELHOS[0], consulta)}
        proximo = 1
        while pendentes:
            prazo = atraso_reserva_s if proximo < len(ESPELHOS) else None
            prontos, pendentes = wait(pendentes, timeout=prazo, return_when=FIRST_COMPLETED)
            for f in prontos:
                try:
                    return f.result()
                except (OverpassIndisponivel, httpx.HTTPError, json.JSONDecodeError) as e:
                    erros.append(str(e) or type(e).__name__)
            if proximo < len(ESPELHOS) and (not prontos or not pendentes):
                pendentes.add(ex.submit(_post, ESPELHOS[proximo], consulta))
                proximo += 1
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    raise OverpassIndisponivel("; ".join(erros) or "sem resposta")


DIETA_OSM = {
    "diet:vegetarian": "vegetariano",
    "diet:vegan": "vegano",
    "diet:gluten_free": "sem_gluten",
    "diet:lactose_free": "sem_lactose",
}


def _endereco(tags: dict[str, str]) -> str:
    rua = tags.get("addr:street", "")
    num = tags.get("addr:housenumber", "")
    bairro = tags.get("addr:suburb", "") or tags.get("addr:neighbourhood", "")
    partes = [f"{rua}, {num}".strip(", ") if rua else "", bairro]
    return " - ".join(p for p in partes if p)


def normalizar_elemento(e: dict[str, Any]) -> dict[str, Any] | None:
    tags = e.get("tags", {})
    nome = (tags.get("name") or "").strip()
    if not nome:
        return None
    lat = e.get("lat") or (e.get("center") or {}).get("lat")
    lon = e.get("lon") or (e.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    categorias, rotulos, origem = classificar(tags)
    dieta = sorted({v for k, v in DIETA_OSM.items() if tags.get(k) in ("yes", "only")})
    cozinha = tags.get("cuisine", "").lower()
    if "vegan" in cozinha:
        dieta = sorted(set(dieta) | {"vegano", "vegetariano"})
    elif "vegetarian" in cozinha:
        dieta = sorted(set(dieta) | {"vegetariano"})
    pratos = tipicos(categorias, rotulos, dieta)
    rotulos_txt = " ".join(rotulos + [rotulo_categoria(c) for c in categorias])
    busca_texto = sem_acento(f"{nome} {rotulos_txt} {' '.join(p.nome for p in pratos[:12])}")
    entrega = tags.get("delivery")
    return {
        "id": f"osm:{e['type'][0]}{e['id']}",
        "nome": nome,
        "tipo": tags.get("amenity") or tags.get("shop") or "",
        "categorias": categorias,
        "rotulos": rotulos,
        "origem_categoria": origem,
        "lat": float(lat),
        "lon": float(lon),
        "endereco": _endereco(tags),
        "horario": tags.get("opening_hours"),
        "site": tags.get("website") or tags.get("contact:website") or tags.get("contact:instagram"),
        "telefone": tags.get("phone") or tags.get("contact:phone"),
        "dieta": dieta,
        "entrega": None if entrega is None else entrega == "yes",
        "tags": tags,
        "busca_texto": busca_texto,
    }


def texto_embedding(lugar: dict[str, Any]) -> str:
    pratos = tipicos(lugar["categorias"], lugar["rotulos"])
    cozinha = ", ".join(lugar["rotulos"] or [rotulo_categoria(c) for c in lugar["categorias"]]) or "restaurante"
    return f"{lugar['nome']}. {cozinha}. Costuma ter: {', '.join(p.nome for p in pratos[:8])}."


def gravar_lugares(conn, lugares: list[dict[str, Any]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """insert into lugares (id, nome, tipo, categorias, rotulos, origem_categoria, lat, lon, endereco, horario,
                                    site, telefone, dieta, entrega, tags, busca_texto)
               values (%(id)s, %(nome)s, %(tipo)s, %(categorias)s, %(rotulos)s, %(origem_categoria)s, %(lat)s, %(lon)s,
                       %(endereco)s, %(horario)s, %(site)s, %(telefone)s, %(dieta)s, %(entrega)s, %(tags)s,
                       %(busca_texto)s)
               on conflict (id) do update set nome = excluded.nome, tipo = excluded.tipo,
                 categorias = excluded.categorias, rotulos = excluded.rotulos,
                 origem_categoria = excluded.origem_categoria, lat = excluded.lat, lon = excluded.lon,
                 endereco = excluded.endereco, horario = excluded.horario, site = excluded.site,
                 telefone = excluded.telefone, dieta = excluded.dieta, entrega = excluded.entrega,
                 tags = excluded.tags, busca_texto = excluded.busca_texto, atualizado_em = now()""",
            [{**lugar, "tags": Jsonb(lugar["tags"])} for lugar in lugares],
        )


def reclassificar(pool) -> int:
    """Refaz a classificação a partir das tags guardadas (depois de mudar as regras de categoria).
    Só mexe nas linhas cuja categoria mudou e zera o embedding delas para recalcular."""
    with pool.connection() as conn:
        linhas = conn.execute(
            "select id, lat, lon, tags, categorias from lugares where origem_categoria <> 'osm'"
        ).fetchall()
        mudancas = []
        for r in linhas:
            tipo, num = r["id"].removeprefix("osm:")[0], r["id"].removeprefix("osm:")[1:]
            novo = normalizar_elemento({"type": tipo, "id": num, "lat": r["lat"], "lon": r["lon"], "tags": r["tags"]})
            if novo and novo["categorias"] != list(r["categorias"]):
                mudancas.append(novo)
        with conn.cursor() as cur:
            cur.executemany(
                """update lugares set categorias = %(categorias)s, rotulos = %(rotulos)s,
                          origem_categoria = %(origem_categoria)s, busca_texto = %(busca_texto)s, embedding = null
                    where id = %(id)s""",
                mudancas,
            )
    return len(mudancas)


def calcular_embeddings(pool, embedder, lote: int = 128) -> int:
    """Preenche embeddings que faltam. Roda em thread; devolve quantos calculou."""
    total = 0
    while True:
        with pool.connection() as conn:
            linhas = conn.execute(
                "select id, nome, categorias, rotulos from lugares where embedding is null limit %s", (lote,)
            ).fetchall()
            if not linhas:
                return total
            vetores = embedder.embed([texto_embedding(dict(r)) for r in linhas])
            with conn.cursor() as cur:
                cur.executemany(
                    "update lugares set embedding = %s where id = %s",
                    [(v, r["id"]) for v, r in zip(vetores, linhas, strict=True)],
                )
            total += len(linhas)


_locks: dict[tuple[int, int], threading.Lock] = {}
_locks_guarda = threading.Lock()
_thread_embeddings: threading.Thread | None = None


def _lock_da_regiao(lat: float, lon: float) -> threading.Lock:
    chave = (round(lat * 50), round(lon * 50))  # células de ~2 km
    with _locks_guarda:
        return _locks.setdefault(chave, threading.Lock())


def area_coberta(conn, lat: float, lon: float) -> dict | None:
    return conn.execute(
        """select id, raio_m, n_lugares, atualizado_em from areas
           where haversine_km(lat, lon, %s, %s) <= least(%s, raio_m / 1000.0 - 1.0)
             and atualizado_em > now() - make_interval(days => %s)
           order by atualizado_em desc limit 1""",
        (lat, lon, COBERTURA_KM, VALIDADE_DIAS),
    ).fetchone()


def garantir_area(pool, embedder, lat: float, lon: float, carregar=None) -> dict[str, Any]:
    """Garante que os lugares em volta do ponto estão no banco. Devolve um resumo para a interface."""
    # Resolvido na chamada (e não como valor padrão): assim os testes conseguem trocar a fonte.
    carregar = carregar or consultar_overpass
    with pool.connection() as conn:
        area = area_coberta(conn, lat, lon)
    if area:
        return {"baixou": False, "n_lugares": area["n_lugares"], "raio_m": area["raio_m"]}

    with _lock_da_regiao(lat, lon):
        with pool.connection() as conn:  # outra requisição pode ter baixado enquanto esperávamos
            area = area_coberta(conn, lat, lon)
        if area:
            return {"baixou": False, "n_lugares": area["n_lugares"], "raio_m": area["raio_m"]}

        inicio = time.perf_counter()
        raio = RAIO_PADRAO_M
        brutos = carregar(lat, lon, raio)
        lugares = [x for x in (normalizar_elemento(e) for e in brutos) if x]
        if len(lugares) < MINIMO_LUGARES:
            raio = RAIO_CIDADE_PEQUENA_M
            lugares = [x for x in (normalizar_elemento(e) for e in carregar(lat, lon, raio)) if x]
        with pool.connection() as conn, conn.transaction():
            gravar_lugares(conn, lugares)
            conn.execute(
                "insert into areas (lat, lon, raio_m, n_lugares, fonte) values (%s, %s, %s, %s, 'overpass')",
                (lat, lon, raio, len(lugares)),
            )
        log.info(
            "área (%.4f, %.4f) r=%dm: %d lugares em %.1fs", lat, lon, raio, len(lugares), time.perf_counter() - inicio
        )

    disparar_embeddings(pool, embedder)
    return {
        "baixou": True,
        "n_lugares": len(lugares),
        "raio_m": raio,
        "segundos": round(time.perf_counter() - inicio, 1),
    }


def disparar_embeddings(pool, embedder) -> None:
    global _thread_embeddings
    with _locks_guarda:
        if _thread_embeddings and _thread_embeddings.is_alive():
            return

        def rodar():
            try:
                n = calcular_embeddings(pool, embedder)
                if n:
                    log.info("embeddings calculados para %d lugares", n)
            except Exception:  # noqa: BLE001 - thread de fundo: registra e segue, a busca vive sem vetores
                log.exception("falha ao calcular embeddings")

        _thread_embeddings = threading.Thread(target=rodar, name="embeddings-lugares", daemon=True)
        _thread_embeddings.start()


def aquecer(pool, embedder, pastas: list[Path]) -> list[dict[str, Any]]:
    """Carrega recortes salvos (data/recortes, data/aquecimento) no banco, sem consultar a internet."""
    resumo = []
    for pasta in pastas:
        for arq in sorted(Path(pasta).glob("*.json")):
            d = json.loads(arq.read_text(encoding="utf-8"))
            meta = d["meta"]
            lugares = [x for x in (normalizar_elemento(e) for e in d["elements"]) if x]
            with pool.connection() as conn, conn.transaction():
                gravar_lugares(conn, lugares)
                if not area_coberta(conn, meta["lat"], meta["lon"]):
                    conn.execute(
                        "insert into areas (lat, lon, raio_m, n_lugares, fonte) values (%s, %s, %s, %s, %s)",
                        (meta["lat"], meta["lon"], meta["raio_m"], len(lugares), f"recorte:{arq.stem}"),
                    )
            resumo.append({"recorte": arq.stem, "lugares": len(lugares)})
    disparar_embeddings(pool, embedder)
    return resumo


# ---------------------------------------------------------------- primeira leva rápida (Nominatim)

TIPOS_RAPIDOS = ["restaurant", "fast_food", "bakery", "cafe", "ice_cream"]  # o mais pedido primeiro
RAIO_RAPIDO_KM = 2.5


def _de_nominatim(item: dict[str, Any]) -> dict[str, Any] | None:
    """Converte um resultado do Nominatim no formato de elemento do Overpass (mesmo id, mesmas tags)."""
    tipo = {"node": "node", "way": "way", "relation": "relation"}.get(item.get("osm_type", ""))
    if not tipo or not item.get("name"):
        return None
    end = item.get("address") or {}
    tags = {k: v for k, v in (item.get("extratags") or {}).items() if isinstance(v, str)}
    tags.update({"name": item["name"], item.get("category", "amenity"): item.get("type", "")})
    for chave_osm, chave_end in (
        ("addr:street", "road"),
        ("addr:housenumber", "house_number"),
        ("addr:suburb", "suburb"),
    ):
        if end.get(chave_end):
            tags[chave_osm] = end[chave_end]
    return {"type": tipo, "id": int(item["osm_id"]), "lat": float(item["lat"]), "lon": float(item["lon"]), "tags": tags}


def consultar_rapido(lat: float, lon: float, raio_km: float = RAIO_RAPIDO_KM):
    """Gera lotes de lugares, um por tipo, em ~1 s cada (o Nominatim limita a 1 requisição/s; o lock em
    cep.py garante). Cada lote pode ser gravado assim que chega."""
    import math

    from bocado.local.cep import _nominatim

    dlat = raio_km / 111.0
    dlon = raio_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    caixa = f"{lon - dlon:.5f},{lat + dlat:.5f},{lon + dlon:.5f},{lat - dlat:.5f}"
    with httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0), headers={"User-Agent": user_agent()}) as c:
        for tipo in TIPOS_RAPIDOS:
            try:
                itens = _nominatim(
                    c, amenity=tipo, viewbox=caixa, bounded=1, format="jsonv2", limit=40, extratags=1, addressdetails=1
                )
            except Exception as e:  # noqa: BLE001 - a leva rápida é um bônus; o Overpass vem depois
                log.info("leva rápida (%s) falhou: %s", tipo, e)
                continue
            yield [el for el in (_de_nominatim(item) for item in itens) if el]


# ---------------------------------------------------------------- download em segundo plano

_jobs: dict[tuple[int, int], dict[str, Any]] = {}


def _chave(lat: float, lon: float) -> tuple[int, int]:
    return (round(lat * 50), round(lon * 50))


def estado_area(pool, lat: float, lon: float) -> dict[str, Any]:
    """'pronta' (com n_lugares), 'carregando' ou 'erro'. Não dispara nada."""
    with pool.connection() as conn:
        area = area_coberta(conn, lat, lon)
    if area:
        return {"estado": "pronta", "n_lugares": area["n_lugares"], "raio_m": area["raio_m"]}
    job = _jobs.get(_chave(lat, lon))
    if job and job["estado"] == "carregando" and job.get("n_parcial"):
        return {"estado": "parcial", "n_lugares": job["n_parcial"], "desde_s": round(time.monotonic() - job["inicio"])}
    if job and job["estado"] == "carregando":
        return {"estado": "carregando", "desde_s": round(time.monotonic() - job["inicio"])}
    if job and job["estado"] == "parcial_final":
        return {"estado": "parcial", "n_lugares": job["n_parcial"], "completo": False}
    if job and job["estado"] == "erro":
        return {"estado": "erro", "erro": job["erro"], "ha_s": round(time.monotonic() - job["fim"])}
    return {"estado": "pendente"}


def iniciar_area(pool, embedder, lat: float, lon: float) -> dict[str, Any]:
    """Garante a região numa thread e devolve na hora o estado atual (a pessoa entra sem esperar)."""
    atual = estado_area(pool, lat, lon)
    if atual["estado"] in ("pronta", "parcial", "carregando") or (atual["estado"] == "erro" and atual["ha_s"] < 60):
        return atual  # depois de um erro, espera 1 minuto antes de tentar de novo
    chave = _chave(lat, lon)
    _jobs[chave] = {"estado": "carregando", "inicio": time.monotonic(), "erro": None}

    def rodar():
        # 1ª etapa (segundos): Nominatim traz lugares reais tipo a tipo, já buscáveis.
        try:
            for lote in consultar_rapido(lat, lon):
                lugares = [x for x in (normalizar_elemento(e) for e in lote) if x]
                if not lugares:
                    continue
                with pool.connection() as conn, conn.transaction():
                    gravar_lugares(conn, lugares)
                _jobs[chave]["n_parcial"] = _jobs[chave].get("n_parcial", 0) + len(lugares)
            log.info(
                "leva rápida (%.4f, %.4f): %d lugares em %.1fs",
                lat,
                lon,
                _jobs[chave].get("n_parcial", 0),
                time.monotonic() - _jobs[chave]["inicio"],
            )
            disparar_embeddings(pool, embedder)
        except Exception as e:  # noqa: BLE001 - segue para o Overpass
            log.info("leva rápida falhou: %s", e)
        # 2ª etapa (até ~1 min): Overpass completa a região.
        try:
            garantir_area(pool, embedder, lat, lon)
            _jobs[chave]["estado"] = "pronta"
        except Exception as e:  # noqa: BLE001 - o estado vira "erro" e a interface oferece tentar de novo
            log.warning("falha ao baixar a região (%.4f, %.4f): %s", lat, lon, e)
            if _jobs[chave].get("n_parcial"):
                _jobs[chave]["estado"] = "parcial_final"  # fica com a leva rápida
                return
            _jobs[chave].update(estado="erro", erro="O mapa (OpenStreetMap) não respondeu agora.", fim=time.monotonic())

    threading.Thread(target=rodar, name=f"area-{chave}", daemon=True).start()
    return {"estado": "carregando", "desde_s": 0}
