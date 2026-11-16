"""CEP -> endereço (ViaCEP, com BrasilAPI de reserva) -> coordenadas (Nominatim/OpenStreetMap).

Tudo fica em cache no banco: cada CEP é consultado nas APIs externas uma vez só.
O Nominatim pede no máximo 1 requisição por segundo e um User-Agent identificável;
o lock global abaixo garante isso dentro do processo.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass

import httpx

from bocado import __version__

log = logging.getLogger(__name__)


def user_agent() -> str:
    """Identificação pedida pela política de uso do Nominatim: nome, versão e, se houver, o repositório."""
    from bocado.config import get_settings

    repo = get_settings().repo_url
    return f"bora-de-que/{__version__}" + (f" (+{repo})" if repo else "")


TIMEOUT = httpx.Timeout(15.0, connect=8.0)

# Fuso por UF (o Brasil tem quatro). O resto do país usa America/Sao_Paulo.
FUSO_POR_UF = {
    "AC": "America/Rio_Branco",
    "AM": "America/Manaus",
    "RR": "America/Boa_Vista",
    "RO": "America/Porto_Velho",
    "MT": "America/Cuiaba",
    "MS": "America/Campo_Grande",
}


class CepInvalido(ValueError):
    pass


class CepNaoEncontrado(LookupError):
    pass


class ServicoIndisponivel(RuntimeError):
    pass


@dataclass
class Local:
    cep: str
    logradouro: str
    bairro: str
    cidade: str
    uf: str
    ibge: str
    lat: float
    lon: float
    precisao: str  # "rua" | "bairro" | "cidade"

    @property
    def fuso(self) -> str:
        return FUSO_POR_UF.get(self.uf, "America/Sao_Paulo")

    @property
    def rotulo(self) -> str:
        return f"{self.bairro}, {self.cidade}" if self.bairro else f"{self.cidade} - {self.uf}"

    def dict(self) -> dict:
        return asdict(self) | {"rotulo": self.rotulo, "fuso": self.fuso}


def normalizar_cep(cep: str) -> str:
    digitos = re.sub(r"\D", "", cep or "")
    if len(digitos) != 8 or digitos == "00000000":
        raise CepInvalido("CEP precisa ter 8 dígitos")
    return digitos


_lock_nominatim = threading.Lock()
_ultimo_nominatim = 0.0


def _get(cliente: httpx.Client, url: str, **kw) -> httpx.Response:
    try:
        r = cliente.get(url, **kw)
    except httpx.HTTPError as e:
        raise ServicoIndisponivel(f"{url.split('/')[2]}: {e}") from e
    if r.status_code >= 500 or r.status_code == 429:
        raise ServicoIndisponivel(f"{url.split('/')[2]} respondeu {r.status_code}")
    return r


def consultar_endereco(cep: str, cliente: httpx.Client) -> dict:
    """ViaCEP primeiro; se cair, BrasilAPI."""
    try:
        r = _get(cliente, f"https://viacep.com.br/ws/{cep}/json/")
        if r.status_code == 400:
            raise CepInvalido("CEP inválido")
        d = r.json()
        if d.get("erro"):
            raise CepNaoEncontrado("CEP não encontrado")
        return {
            "logradouro": d.get("logradouro", ""),
            "bairro": d.get("bairro", ""),
            "cidade": d["localidade"],
            "uf": d["uf"],
            "ibge": d.get("ibge", ""),
        }
    except ServicoIndisponivel as e:
        log.warning("ViaCEP indisponível (%s); tentando BrasilAPI", e)
    r = _get(cliente, f"https://brasilapi.com.br/api/cep/v1/{cep}")
    if r.status_code == 404:
        raise CepNaoEncontrado("CEP não encontrado")
    d = r.json()
    return {
        "logradouro": d.get("street", "") or "",
        "bairro": d.get("neighborhood", "") or "",
        "cidade": d["city"],
        "uf": d["state"],
        "ibge": "",
    }


def _nominatim(cliente: httpx.Client, **params) -> list[dict]:
    global _ultimo_nominatim
    with _lock_nominatim:
        espera = 1.1 - (time.monotonic() - _ultimo_nominatim)
        if espera > 0:
            time.sleep(espera)
        try:
            r = _get(
                cliente,
                "https://nominatim.openstreetmap.org/search",
                params={"format": "json", "limit": 1, "countrycodes": "br", **params},
            )
        finally:
            _ultimo_nominatim = time.monotonic()
    return r.json() if r.status_code == 200 else []


def geocodificar(end: dict, cliente: httpx.Client) -> tuple[float, float, str]:
    """Tenta rua, depois bairro, depois cidade. Devolve (lat, lon, precisão)."""
    tentativas = []
    if end["logradouro"]:
        tentativas.append(("rua", {"street": end["logradouro"], "city": end["cidade"], "state": end["uf"]}))
    if end["bairro"]:
        tentativas.append(("bairro", {"q": f"{end['bairro']}, {end['cidade']}, {end['uf']}"}))
    tentativas.append(("cidade", {"city": end["cidade"], "state": end["uf"]}))
    for precisao, params in tentativas:
        res = _nominatim(cliente, **params)
        if res:
            return float(res[0]["lat"]), float(res[0]["lon"]), precisao
    raise ServicoIndisponivel("não consegui localizar o endereço no mapa")


def resolver(cep: str, conn=None) -> Local:
    """CEP -> Local, usando o cache do banco quando houver."""
    cep = normalizar_cep(cep)
    if conn is not None:
        row = conn.execute("select * from ceps where cep = %s", (cep,)).fetchone()
        if row:
            return Local(
                cep=cep,
                logradouro=row["logradouro"],
                bairro=row["bairro"],
                cidade=row["cidade"],
                uf=row["uf"],
                ibge=row["ibge"],
                lat=row["lat"],
                lon=row["lon"],
                precisao=row["precisao"],
            )
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": user_agent()}) as cliente:
        end = consultar_endereco(cep, cliente)
        lat, lon, precisao = geocodificar(end, cliente)
    local = Local(cep=cep, lat=lat, lon=lon, precisao=precisao, **end)
    if conn is not None:
        conn.execute(
            """insert into ceps (cep, logradouro, bairro, cidade, uf, ibge, lat, lon, precisao)
               values (%(cep)s, %(logradouro)s, %(bairro)s, %(cidade)s, %(uf)s, %(ibge)s, %(lat)s, %(lon)s, %(precisao)s)
               on conflict (cep) do nothing""",
            asdict(local),
        )
    return local
