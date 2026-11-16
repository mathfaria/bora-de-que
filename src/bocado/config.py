from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=RAIZ / ".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = ""

    llm_provider: Literal["gemini", "none"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    # "low" reduz latência; vazio deixa o padrão do modelo
    gemini_thinking_level: str = "low"
    llm_timeout_s: float = 40.0

    # Preço de tabela do gemini-3.5-flash-lite (US$ por 1M tokens), usado para calcular o custo
    # equivalente mesmo no plano gratuito. Fonte: página de preços da API Gemini, nov/2026.
    # O padrão é o flash-lite porque o plano gratuito dá 20 requisições/dia aos modelos Flash
    # e 15 por minuto ao flash-lite (medido em 13/11/2026).
    preco_entrada_por_mtok: float = 0.30
    preco_saida_por_mtok: float = 2.50

    extractor: Literal["rules", "llm", "finetuned"] = "rules"
    finetuned_model: str = "models/bocado-extrator"

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: str = str(RAIZ / ".cache" / "fastembed")

    agente_max_passos: int = 6

    rate_limit_per_ip: int = 20
    rate_limit_window_s: int = 600
    # Teto diário de turnos com LLM na demo pública; acima disso, modo simples.
    llm_turnos_por_dia: int = 300

    repo_url: str = ""

    @property
    def llm_ativo(self) -> bool:
        return self.llm_provider == "gemini" and bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
