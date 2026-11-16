"""O que costuma ter em cada tipo de casa, e o que é típico de cada estado.

Não existe fonte aberta de cardápios reais. Em vez de inventar o cardápio de um restaurante real,
o app sugere pratos TÍPICOS da cozinha daquele lugar (rotulados como tal) e pratos regionais do
estado do usuário. A biblioteca por cozinha é a mesma escrita à mão em datagen/library.py, sem
preços; as tags (vegano, sem glúten...) valem para o prato típico, não para o restaurante.
"""

from __future__ import annotations

from dataclasses import dataclass

from bocado.datagen.library import POOLS


@dataclass(frozen=True)
class PratoTipico:
    nome: str
    descricao: str
    tags: tuple[str, ...]


def _de_pool(*chaves: str) -> list[PratoTipico]:
    out: list[PratoTipico] = []
    vistos: set[str] = set()
    for chave in chaves:
        for nome, desc, _preco, _kcal, tags, _serve in POOLS[chave][1]:
            base = nome.split(" (")[0]
            if base not in vistos:
                vistos.add(base)
                out.append(PratoTipico(base, desc, tuple(sorted(tags.split()))))
    return out


# O pool vegano (brasileira_vegana) não entra no típico de "brasileira": um restaurante brasileiro comum
# não costuma ter feijoada vegana. Ele só vale para casas veganas/vegetarianas (ver tipicos()).
TIPICOS_POR_CATEGORIA: dict[str, list[PratoTipico]] = {
    cat: _de_pool(*[k for k, (c, _) in POOLS.items() if c == cat and k != "brasileira_vegana"])
    for cat in {c for c, _ in POOLS.values()}
}


def _p(nome: str, desc: str, tags: str = "") -> PratoTipico:
    return PratoTipico(nome, desc, tuple(sorted(tags.split())))


TIPICOS_POR_ROTULO: dict[str, list[PratoTipico]] = {
    "portuguesa": [
        _p(
            "Bacalhau à Gomes de Sá", "Bacalhau desfiado com batata, cebola, ovo e azeitona", "frutos_do_mar sem_gluten"
        ),
        _p(
            "Polvo à lagareiro",
            "Polvo assado com batatas ao murro e muito azeite",
            "frutos_do_mar sem_gluten sem_lactose",
        ),
        _p("Pastel de nata", "Massa folhada com creme de ovos", "vegetariano"),
    ],
    "peruana": [
        _p(
            "Ceviche",
            "Peixe branco marinado no limão com cebola roxa e pimenta",
            "frutos_do_mar sem_gluten sem_lactose leve apimentado",
        ),
        _p("Lomo saltado", "Tiras de carne salteadas com cebola, tomate e batata frita", "carne_vermelha frito"),
        _p("Causa limeña", "Purê de batata amarela em camadas com recheio", "sem_gluten"),
    ],
    "francesa": [
        _p("Steak frites", "Bife grelhado com batatas fritas", "carne_vermelha frito"),
        _p("Quiche lorraine", "Torta de creme com bacon", "porco"),
        _p("Crème brûlée", "Creme com crosta de açúcar queimado", "vegetariano sem_gluten"),
    ],
    "coreana": [
        _p("Bibimbap", "Arroz com legumes, ovo e molho gochujang", "apimentado"),
        _p("Frango frito coreano", "Frango empanado com molho agridoce picante", "frito apimentado"),
        _p("Kimchi jjigae", "Ensopado de kimchi com tofu", "apimentado"),
    ],
    "indiana": [
        _p("Frango tikka masala", "Frango ao molho de tomate, creme e especiarias", "sem_gluten proteico"),
        _p("Dal", "Lentilhas cozidas com especiarias", "vegano vegetariano sem_gluten sem_lactose"),
        _p("Samosa", "Pastel frito de batata e ervilha", "vegetariano frito"),
    ],
    "espanhola": [
        _p("Paella", "Arroz com frutos do mar e açafrão", "frutos_do_mar sem_gluten sem_lactose"),
        _p("Tortilla española", "Omelete de batata e cebola", "vegetariano sem_gluten"),
    ],
    "grega": [
        _p("Gyros", "Pão pita com carne, tomate e tzatziki", "carne_vermelha"),
        _p("Salada grega", "Pepino, tomate, azeitonas e feta", "vegetariano sem_gluten leve"),
    ],
    "frutos do mar": [
        _p("Moqueca de peixe", "Peixe no leite de coco e dendê", "frutos_do_mar sem_gluten sem_lactose"),
        _p("Camarão na moranga", "Camarão cremoso servido na abóbora", "frutos_do_mar sem_gluten"),
        _p("Isca de peixe", "Tiras de peixe empanadas e fritas", "frutos_do_mar frito"),
    ],
    "peixes": [
        _p("Peixe grelhado com legumes", "Filé de peixe na chapa com legumes", "frutos_do_mar sem_gluten leve proteico")
    ],
}

# Para restaurantes veganos/vegetarianos o típico é o pool vegano da biblioteca.
TIPICOS_POR_ROTULO["vegana"] = _de_pool("brasileira_vegana")
TIPICOS_POR_ROTULO["vegetariana"] = _de_pool("brasileira_vegana")


def tipicos(categorias: list[str], rotulos: list[str], dieta: list[str] | tuple[str, ...] = ()) -> list[PratoTipico]:
    """Pratos típicos de uma casa. Se o OSM confirma que ela é vegana/vegetariana, os veganos vêm primeiro."""
    base: list[PratoTipico] = []
    for r in rotulos:
        if r in TIPICOS_POR_ROTULO:
            base = list(TIPICOS_POR_ROTULO[r])
            break
    if not base:
        for c in categorias:
            base += TIPICOS_POR_CATEGORIA.get(c, [])
    if "vegano" in dieta or "vegetariano" in dieta:
        # Casa confirmada como vegana/vegetariana: só entram pratos compatíveis (não serve salmão).
        marca = "vegano" if "vegano" in dieta else "vegetariano"
        veganos = [p for p in TIPICOS_POR_ROTULO["vegana"] if p not in base]
        base = [p for p in base if marca in p.tags] + veganos
    return base


MINIMO_COSTUMA_TER = 2  # "costuma ter opção X" exige pelo menos 2 pratos típicos compatíveis


@dataclass(frozen=True)
class PratoRegional:
    nome: str
    descricao: str
    onde: tuple[str, ...]  # categorias de casa onde costuma aparecer
    termos: tuple[str, ...] = ()  # palavras no nome do lugar que indicam que ele serve o prato


def _r(nome: str, desc: str, onde: str, termos: str = "") -> PratoRegional:
    return PratoRegional(nome, desc, tuple(onde.split()), tuple(t for t in termos.split("|") if t))


REGIONAIS: dict[str, list[PratoRegional]] = {
    "AC": [
        _r("Tacacá", "Caldo de tucupi com goma de tapioca, jambu e camarão seco", "nordestina brasileira", "tacaca"),
        _r("Baixaria", "Cuscuz de milho com carne moída e ovo frito", "brasileira cafe"),
        _r("Rabada no tucupi", "Rabada cozida no tucupi com jambu", "brasileira"),
    ],
    "AL": [
        _r("Sururu ao coco", "Molusco da lagoa cozido no leite de coco", "nordestina brasileira", "sururu"),
        _r("Tapioca", "Tapioca recheada, doce ou salgada", "nordestina cafe", "tapioca"),
        _r("Peixe na telha", "Peixe assado em telha de barro com legumes", "brasileira"),
    ],
    "AM": [
        _r("Tambaqui na brasa", "Costela de tambaqui assada na brasa", "churrasco brasileira", "tambaqui|peixaria"),
        _r("X-caboquinho", "Pão com tucumã, queijo coalho e banana frita", "lanches cafe", "caboquinho|tucuma"),
        _r("Tacacá", "Caldo de tucupi com jambu e camarão", "nordestina brasileira", "tacaca"),
    ],
    "AP": [
        _r("Camarão no bafo", "Camarão regional cozido no vapor", "brasileira"),
        _r("Açaí com peixe frito", "Açaí sem açúcar acompanhando peixe frito", "acai brasileira", "acai"),
    ],
    "BA": [
        _r(
            "Acarajé",
            "Bolinho de feijão-fradinho frito no dendê com vatapá e camarão",
            "nordestina lanches",
            "acaraje|baiana",
        ),
        _r("Moqueca baiana", "Peixe ou camarão no dendê e leite de coco", "nordestina brasileira", "moqueca"),
        _r("Abará", "Massa de feijão cozida na folha de bananeira", "nordestina", "acaraje"),
        _r("Bobó de camarão", "Creme de mandioca com camarão e dendê", "nordestina brasileira"),
    ],
    "CE": [
        _r(
            "Baião de dois", "Arroz com feijão-de-corda, queijo coalho e carne de sol", "nordestina brasileira", "baiao"
        ),
        _r(
            "Carne de sol com macaxeira",
            "Carne de sol na manteiga de garrafa com mandioca",
            "nordestina brasileira",
            "carne de sol",
        ),
        _r("Panelada", "Cozido de bucho e miúdos bovinos", "nordestina"),
    ],
    "DF": [
        _r("Galinhada", "Arroz com frango, açafrão e pequi", "brasileira"),
        _r("Pastel com caldo de cana", "Pastel frito com caldo de cana gelado", "lanches", "pastel"),
    ],
    "ES": [
        _r(
            "Moqueca capixaba",
            "Peixe na panela de barro, sem dendê e sem leite de coco",
            "brasileira",
            "moqueca|moqueca capixaba",
        ),
        _r("Torta capixaba", "Torta de bacalhau, palmito e frutos do mar", "brasileira"),
    ],
    "GO": [
        _r("Empadão goiano", "Empadão de frango, linguiça, guariroba e queijo", "brasileira cafe", "empad"),
        _r("Pamonha", "Pamonha de milho verde, doce ou salgada", "cafe lanches", "pamonha"),
        _r("Arroz com pequi", "Arroz com frango e pequi", "brasileira"),
    ],
    "MA": [
        _r("Arroz de cuxá", "Arroz com vinagreira, camarão seco e gergelim", "nordestina brasileira"),
        _r("Torta de camarão", "Torta assada de camarão com ovos", "nordestina brasileira"),
    ],
    "MG": [
        _r("Pão de queijo", "Pão de queijo mineiro assado na hora", "cafe lanches", "pao de queijo|padaria"),
        _r("Feijão tropeiro", "Feijão com farinha, linguiça, torresmo, ovo e couve", "brasileira", "mineir|tropeiro"),
        _r("Frango com quiabo", "Frango ensopado com quiabo e angu", "brasileira", "mineir"),
        _r("Tutu de feijão", "Feijão engrossado com farinha, servido com couve e linguiça", "brasileira", "mineir"),
    ],
    "MS": [
        _r("Sopa paraguaia", "Torta salgada de milho com queijo", "brasileira cafe"),
        _r("Chipa", "Rosquinha de polvilho com queijo", "cafe lanches", "chipa"),
        _r("Sobá", "Caldo com macarrão, carne e omelete, herança okinawana", "japonesa", "soba"),
    ],
    "MT": [
        _r("Peixe pacu assado", "Pacu recheado assado", "brasileira churrasco", "peixaria|pacu"),
        _r("Maria Isabel", "Arroz com carne seca picada", "brasileira"),
    ],
    "PA": [
        _r("Tacacá", "Caldo de tucupi com goma, jambu e camarão", "nordestina brasileira", "tacaca"),
        _r("Maniçoba", "Folha de mandioca cozida por dias com carnes de porco", "brasileira"),
        _r("Açaí com peixe frito", "Açaí sem açúcar com peixe frito e farinha", "acai brasileira", "acai"),
        _r("Pato no tucupi", "Pato cozido no tucupi com jambu", "brasileira"),
    ],
    "PB": [
        _r(
            "Carne de sol com queijo coalho",
            "Carne de sol grelhada com queijo coalho e macaxeira",
            "nordestina brasileira",
            "carne de sol",
        ),
        _r("Rubacão", "Arroz com feijão verde, queijo coalho e nata", "nordestina"),
    ],
    "PE": [
        _r("Bolo de rolo", "Bolo enrolado em camadas finas com goiabada", "doces cafe", "bolo de rolo"),
        _r("Tapioca", "Tapioca recheada, doce ou salgada", "nordestina cafe", "tapioca"),
        _r(
            "Carne de sol com macaxeira",
            "Carne de sol na manteiga de garrafa com macaxeira",
            "nordestina brasileira",
            "carne de sol",
        ),
        _r("Bode guisado", "Carne de bode cozida com temperos", "nordestina brasileira"),
    ],
    "PI": [
        _r("Maria Isabel", "Arroz com carne de sol picada", "nordestina brasileira"),
        _r("Capote", "Galinha-d'angola ensopada", "nordestina brasileira"),
    ],
    "PR": [
        _r("Barreado", "Carne cozida lentamente na panela de barro, com farinha e banana", "brasileira", "barreado"),
        _r("Pierogi", "Pastel cozido de massa recheado com batata e ricota", "brasileira", "pierogi|polon"),
        _r("Carne de onça", "Carne crua moída temperada sobre pão preto", "lanches", "onca"),
    ],
    "RJ": [
        _r("Feijoada", "Feijão preto com carnes, couve, farofa e laranja", "brasileira", "feijoada"),
        _r("Picadinho", "Carne em cubos ao molho com arroz, farofa e ovo", "brasileira"),
        _r("Bolinho de bacalhau", "Bolinho frito de bacalhau, clássico de boteco", "lanches brasileira", "bar|boteco"),
    ],
    "RN": [
        _r("Ginga com tapioca", "Peixinho frito servido na tapioca", "nordestina lanches", "ginga|tapioca"),
        _r("Carne de sol", "Carne de sol com feijão verde e macaxeira", "nordestina brasileira", "carne de sol"),
    ],
    "RO": [_r("Tambaqui assado", "Tambaqui na brasa com farofa", "churrasco brasileira", "tambaqui|peixaria")],
    "RR": [_r("Damorida", "Caldo de peixe com muita pimenta, prato indígena", "brasileira")],
    "RS": [
        _r("Churrasco gaúcho", "Costela e picanha assadas no fogo de chão", "churrasco", "churrascaria|galpao"),
        _r(
            "Galeto al primo canto",
            "Galeto assado com polenta frita e sopa de agnoline",
            "churrasco italiana",
            "galeto|galeteria",
        ),
        _r("Cuca", "Bolo de origem alemã com farofa doce", "cafe doces", "cuca|padaria"),
    ],
    "SC": [
        _r(
            "Sequência de camarão",
            "Camarão em várias preparações, servido em sequência",
            "brasileira",
            "camarao|frutos do mar",
        ),
        _r("Ostras gratinadas", "Ostras de cultivo local gratinadas", "brasileira", "ostra|ostras"),
        _r("Marreco recheado", "Marreco assado com repolho roxo, herança alemã", "brasileira"),
    ],
    "SE": [
        _r("Caranguejo", "Caranguejo cozido no leite de coco", "nordestina brasileira", "caranguejo"),
        _r("Mugunzá salgado", "Milho branco cozido com carnes", "nordestina"),
    ],
    "SP": [
        _r("Virado à paulista", "Tutu, bisteca, couve, ovo, banana e torresmo", "brasileira", "virado"),
        _r(
            "Sanduíche de mortadela",
            "Pão francês com mortadela farta, fama do Mercadão",
            "lanches",
            "mortadela|lanchonete",
        ),
        _r("Pizza paulistana", "Pizza de massa fina e muito recheio", "pizza", "pizzaria"),
        _r("Pastel de feira", "Pastel grande e crocante com caldo de cana", "lanches", "pastel|pastelaria"),
    ],
    "TO": [
        _r("Chambari", "Ossobuco cozido com temperos", "brasileira"),
        _r("Peixe na telha", "Peixe assado na telha de barro", "brasileira"),
    ],
}


def regionais(uf: str) -> list[PratoRegional]:
    return REGIONAIS.get(uf.upper(), [])
