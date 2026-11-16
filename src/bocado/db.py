"""Acesso ao Postgres (pgvector): pool de conexões e esquema."""

from __future__ import annotations

import logging
from importlib import resources

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

TABELAS = ["traces", "mensagens", "conversas", "preferencias", "visitantes", "lugares", "areas", "ceps"]
# Tabelas da primeira versão (catálogo sintético), removidas no reset.
TABELAS_ANTIGAS = ["itens_pedido", "pedidos", "usuarios", "pratos", "restaurantes"]


def garantir_extensao(url: str) -> None:
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("create extension if not exists vector")


def criar_pool(url: str, max_size: int = 5) -> ConnectionPool:
    garantir_extensao(url)
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        # prepare_threshold=None: sem prepared statements, para funcionar atrás de poolers em modo
        # transação (PgBouncer do Neon e do Supabase).
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        configure=register_vector,
        check=ConnectionPool.check_connection,
        open=True,
    )
    pool.wait(timeout=30)
    return pool


def aplicar_schema(conn: psycopg.Connection) -> None:
    sql = resources.files("bocado").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.execute(sql)


def resetar(conn: psycopg.Connection) -> None:
    """Apaga tudo (inclusive o cache de lugares) e recria o esquema."""
    with conn.transaction():
        conn.execute(f"drop table if exists {', '.join(TABELAS + TABELAS_ANTIGAS)} cascade")
        aplicar_schema(conn)
