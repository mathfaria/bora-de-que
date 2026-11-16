"""Baixa e congela recortes reais do OpenStreetMap em data/recortes/ (para testes e avaliações).

O mapa muda com o tempo; congelar garante que a avaliação de hoje e a de daqui a um mês olham
para os mesmos lugares. Dados © colaboradores do OpenStreetMap, licença ODbL.

    python scripts/salvar_recortes.py
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bocado.local.cep import USER_AGENT, _nominatim, consultar_endereco, geocodificar, normalizar_cep
from bocado.local.osm import MINIMO_LUGARES, RAIO_CIDADE_PEQUENA_M, RAIO_PADRAO_M, consultar_overpass

PASTA = Path(__file__).resolve().parents[1] / "data" / "recortes"
# Recortes usados nos testes e nas avaliações (por CEP).
RECORTES = {
    "sao-paulo-paulista": "01310100",
    "belo-horizonte-savassi": "30140071",
    "salvador-barra": "40140130",
    "recife-centro": "50030230",
    "lavras-mg": "37200232",
}

# Cache pré-aquecido para o deploy: centro das capitais e regiões prováveis numa demo (por endereço).
AQUECIMENTO = {
    "rio-branco": ("Centro, Rio Branco", "AC"),
    "maceio": ("Ponta Verde, Maceió", "AL"),
    "macapa": ("Centro, Macapá", "AP"),
    "manaus": ("Centro, Manaus", "AM"),
    "fortaleza": ("Aldeota, Fortaleza", "CE"),
    "brasilia": ("Asa Sul, Brasília", "DF"),
    "vitoria": ("Praia do Canto, Vitória", "ES"),
    "goiania": ("Setor Bueno, Goiânia", "GO"),
    "sao-luis": ("Centro, São Luís", "MA"),
    "cuiaba": ("Centro, Cuiabá", "MT"),
    "campo-grande": ("Centro, Campo Grande", "MS"),
    "belem": ("Nazaré, Belém", "PA"),
    "joao-pessoa": ("Tambaú, João Pessoa", "PB"),
    "curitiba": ("Batel, Curitiba", "PR"),
    "teresina": ("Centro, Teresina", "PI"),
    "rio-de-janeiro": ("Botafogo, Rio de Janeiro", "RJ"),
    "natal": ("Petrópolis, Natal", "RN"),
    "porto-alegre": ("Cidade Baixa, Porto Alegre", "RS"),
    "porto-velho": ("Centro, Porto Velho", "RO"),
    "boa-vista": ("Centro, Boa Vista", "RR"),
    "florianopolis": ("Centro, Florianópolis", "SC"),
    "aracaju": ("Jardins, Aracaju", "SE"),
    "palmas": ("Plano Diretor Sul, Palmas", "TO"),
    "sao-paulo-pinheiros": ("Pinheiros, São Paulo", "SP"),
    "sao-paulo-vila-mariana": ("Vila Mariana, São Paulo", "SP"),
    "osasco": ("Centro, Osasco", "SP"),
    "campinas": ("Cambuí, Campinas", "SP"),
}


def salvar(arq: Path, meta: dict, lat: float, lon: float) -> None:
    raio = RAIO_PADRAO_M
    elementos = consultar_overpass(lat, lon, raio)
    if sum(1 for e in elementos if e.get("tags", {}).get("name")) < MINIMO_LUGARES:
        raio = RAIO_CIDADE_PEQUENA_M
        elementos = consultar_overpass(lat, lon, raio)
    meta = {
        **meta,
        "lat": lat,
        "lon": lon,
        "raio_m": raio,
        "baixado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "fonte": "OpenStreetMap via Overpass API (© colaboradores do OSM, ODbL)",
    }
    arq.write_text(json.dumps({"meta": meta, "elements": elementos}, ensure_ascii=False), encoding="utf-8")
    print(f"{arq.stem}: {len(elementos)} lugares, raio {raio} m", flush=True)


def aquecimento() -> None:
    pasta = PASTA.parent / "aquecimento"
    pasta.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        for nome, (consulta, uf) in AQUECIMENTO.items():
            arq = pasta / f"{nome}.json"
            if arq.exists():
                continue
            res = _nominatim(c, q=f"{consulta}, {uf}, Brasil")
            if not res:
                print(f"{nome}: não geocodificou", flush=True)
                continue
            try:
                salvar(arq, {"consulta": consulta, "uf": uf}, float(res[0]["lat"]), float(res[0]["lon"]))
            except Exception as e:  # noqa: BLE001 - segue para a próxima região
                print(f"{nome}: falhou ({e})", flush=True)
            time.sleep(5)  # gentileza com o Overpass


def main() -> None:
    import sys

    if "--aquecimento" in sys.argv:
        aquecimento()
        return
    PASTA.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        for nome, cep in RECORTES.items():
            arq = PASTA / f"{nome}.json"
            if arq.exists():
                print(f"{nome}: já existe")
                continue
            end = consultar_endereco(normalizar_cep(cep), c)
            lat, lon, precisao = geocodificar(end, c)
            raio = RAIO_PADRAO_M
            elementos = consultar_overpass(lat, lon, raio)
            if sum(1 for e in elementos if e.get("tags", {}).get("name")) < MINIMO_LUGARES:
                raio = RAIO_CIDADE_PEQUENA_M
                elementos = consultar_overpass(lat, lon, raio)
            meta = {
                "cep": cep,
                **end,
                "lat": lat,
                "lon": lon,
                "precisao": precisao,
                "raio_m": raio,
                "baixado_em": datetime.now(UTC).isoformat(timespec="seconds"),
                "fonte": "OpenStreetMap via Overpass API (© colaboradores do OSM, ODbL)",
            }
            arq.write_text(json.dumps({"meta": meta, "elements": elementos}, ensure_ascii=False), encoding="utf-8")
            print(f"{nome}: {len(elementos)} lugares, raio {raio} m ({end['cidade']}/{end['uf']})")
            time.sleep(5)  # gentileza com o Overpass


if __name__ == "__main__":
    main()
