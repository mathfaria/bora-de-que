"""Leitura do `opening_hours` do OpenStreetMap: aberto agora? qual o horário de hoje?

O formato completo é enorme (feriados, semanas do mês, nascer do sol...). Aqui entram os casos
que cobrem quase todo restaurante: "24/7", dias + faixas ("Mo-Fr 11:00-15:00,18:00-23:00"),
"off", e faixas que viram a meia-noite ("18:00-02:00"). Regras posteriores sobrescrevem as
anteriores para os dias que citam (semântica do OSM). Se algo não for entendido, a resposta é
None ("horário não informado"): melhor admitir do que chutar.
"""

from __future__ import annotations

import re
from datetime import datetime

DIAS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
FAIXA = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\+?$")


class NaoEntendido(ValueError):
    pass


def _dias(expr: str) -> list[int]:
    out: list[int] = []
    for parte in expr.split(","):
        parte = parte.strip()
        if "-" in parte:
            a, b = parte.split("-", 1)
            if a not in DIAS or b not in DIAS:
                raise NaoEntendido(parte)
            i, j = DIAS.index(a), DIAS.index(b)
            out += [(i + k) % 7 for k in range((j - i) % 7 + 1)]
        elif parte in DIAS:
            out.append(DIAS.index(parte))
        elif parte == "PH":
            continue  # feriados: ignorados
        else:
            raise NaoEntendido(parte)
    return out


def _faixas(expr: str) -> list[tuple[int, int]]:
    """Faixas em minutos desde 0h do dia; o fim pode passar de 1440 (vira a meia-noite)."""
    out = []
    for parte in expr.split(","):
        m = FAIXA.match(parte.strip())
        if not m:
            raise NaoEntendido(parte)
        h1, m1, h2, m2 = map(int, m.groups())
        ini, fim = h1 * 60 + m1, h2 * 60 + m2
        if fim <= ini:
            fim += 24 * 60
        out.append((ini, fim))
    return out


def interpretar(texto: str) -> dict[int, list[tuple[int, int]]]:
    """dia da semana (0=seg) -> faixas abertas. Lança NaoEntendido."""
    texto = (texto or "").strip()
    if not texto:
        raise NaoEntendido("vazio")
    if texto == "24/7":
        return {d: [(0, 24 * 60)] for d in range(7)}
    semana: dict[int, list[tuple[int, int]]] = {d: [] for d in range(7)}
    regras = [r.strip() for r in texto.split(";") if r.strip()]
    for regra in regras:
        m = re.match(r"^((?:Mo|Tu|We|Th|Fr|Sa|Su|PH)(?:[-,](?:Mo|Tu|We|Th|Fr|Sa|Su|PH))*)\s+(.+)$", regra)
        if m:
            dias, resto = _dias(m.group(1)), m.group(2).strip()
        else:
            dias, resto = list(range(7)), regra
        if resto in ("off", "closed"):
            faixas: list[tuple[int, int]] = []
        elif resto == "24/7" or resto == "00:00-24:00":
            faixas = [(0, 24 * 60)]
        else:
            faixas = _faixas(resto)
        for d in dias:
            semana[d] = list(faixas)
    return semana


def aberto_agora(texto: str | None, agora: datetime) -> bool | None:
    try:
        semana = interpretar(texto or "")
    except NaoEntendido:
        return None
    minuto = agora.hour * 60 + agora.minute
    hoje, ontem = agora.weekday(), (agora.weekday() - 1) % 7
    if any(ini <= minuto < fim for ini, fim in semana[hoje]):
        return True
    # faixa de ontem que passou da meia-noite
    return any(fim > 24 * 60 and minuto < fim - 24 * 60 for _, fim in semana[ontem])


def _hhmm(minutos: int) -> str:
    minutos %= 24 * 60
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def horario_de_hoje(texto: str | None, agora: datetime) -> str | None:
    try:
        semana = interpretar(texto or "")
    except NaoEntendido:
        return None
    faixas = semana[agora.weekday()]
    if not faixas:
        return "fechado hoje"
    if faixas == [(0, 24 * 60)]:
        return "24 horas"
    return ", ".join(f"{_hhmm(a)}–{_hhmm(b)}" for a, b in faixas)


def agora_em(fuso: str) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(fuso))
