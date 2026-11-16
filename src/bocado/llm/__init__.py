from __future__ import annotations

from bocado.llm.base import ClienteLLM

_cliente: ClienteLLM | None = None


def get_llm() -> ClienteLLM | None:
    """Cliente configurado pelo .env, ou None quando não há LLM (modo simples)."""
    global _cliente
    if _cliente is None:
        from bocado.config import get_settings

        s = get_settings()
        if not s.llm_ativo:
            return None
        from bocado.llm.gemini import ClienteGemini

        _cliente = ClienteGemini(s.gemini_api_key, s.gemini_model, s.llm_timeout_s, s.gemini_thinking_level)
    return _cliente


def set_llm(cliente: ClienteLLM | None) -> None:
    global _cliente
    _cliente = cliente
