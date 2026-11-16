"""bora de quê?: agente que sugere pratos e mostra lugares reais perto do CEP."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bocado")
except PackageNotFoundError:  # rodando direto do código, sem instalar
    __version__ = "0.0.0"
