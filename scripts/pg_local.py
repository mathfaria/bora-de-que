"""Sobe um Postgres 16 + pgvector embutido (pacote pgserver), sem Docker e sem instalar nada no sistema.

Uso:  python scripts/pg_local.py      -> imprime a DATABASE_URL local
Os dados ficam em .cache/pgdata. Útil para desenvolvimento e testes; produção usa Neon.
"""

from pathlib import Path

import pgserver

PASTA = Path(__file__).resolve().parents[1] / ".cache" / "pgdata"


def url() -> str:
    PASTA.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(str(PASTA), cleanup_mode=None)
    return srv.get_uri()


if __name__ == "__main__":
    print(url())
