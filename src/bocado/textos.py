import unicodedata


def sem_acento(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# Palavras sem valor de busca (já sem acento, como sai de normalizar()).
STOPWORDS = set(
    """a o as os um uma uns umas de do da dos das em no na nos nas pra pro para por com sem que e ou me
mim eu voce vc quero queria to estou algo alguma algum coisa qualquer bem mais menos muito muita tem ter
hoje agora aqui ai tipo pfv pfvr favor vou bora manda ve sugere indica recomenda oi ola perto casa
comer comida""".split()
)
