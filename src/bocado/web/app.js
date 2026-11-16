"use strict";

const $ = (s) => document.querySelector(s);
const CHAVE = "bora-de-que:visitante";
const estado = { visitante: null, conversa: { id: null, itens: [] }, enviando: false, pollArea: null, contador: 0 };
const MOVIMENTO_REDUZIDO = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const ATALHOS = [
  ["🥗 Algo leve", "quero algo leve, nada de fritura"],
  ["💪 Pós-treino", "algo com bastante proteína pós treino"],
  ["🌱 Vegano", "quero uma opção vegana"],
  ["🌶️ Apimentado", "me sugere algo bem apimentado"],
  ["🎲 Me surpreenda", "não sei o que comer, me surpreenda"],
];
const EXEMPLOS = ["algo leve pro almoço…", "uma comida típica daqui…", "tô com vontade de japonês…", "algo vegano e quentinho…",
  "pós-treino com proteína…", "um doce pra fechar o dia…"];

const CATEGORIA = {
  acai: ["🫐", 0], arabe: ["🧆", 1], brasileira: ["🍛", 2], cafe: ["☕", 0], chinesa: ["🥡", 1],
  churrasco: ["🥩", 2], doces: ["🍰", 0], hamburguer: ["🍔", 1], italiana: ["🍝", 2], japonesa: ["🍣", 0],
  lanches: ["🥪", 1], mexicana: ["🌮", 2], nordestina: ["🥘", 0], pizza: ["🍕", 1], poke: ["🍱", 2],
  saladas: ["🥗", 0], tailandesa: ["🍜", 1],
};
const ROTULO_TAG = {
  vegano: "vegano", vegetariano: "vegetariano", sem_gluten: "sem glúten", sem_lactose: "sem lactose",
  leve: "leve", proteico: "proteico", apimentado: "🌶️ apimentado",
};
const ROTULO_DIETA = { vegetariano: "vegetariano", vegano: "vegano", sem_gluten: "sem glúten", sem_lactose: "sem lactose" };
const ROTULOS_MEMORIA = {
  vegetariano: "vegetariano", vegano: "vegano", sem_gluten: "sem glúten", sem_lactose: "sem lactose",
  fritura: "sem fritura", carne_vermelha: "sem carne vermelha", porco: "sem porco",
  frutos_do_mar: "sem frutos do mar", pimenta: "sem pimenta",
};

/* ------------------------------------------------------------------ utilidades */

function el(tag, attrs = {}, ...filhos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "style") n.setAttribute("style", v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v);
  }
  for (const f of filhos.flat()) {
    if (f === null || f === undefined || f === false) continue;
    n.append(f instanceof Node ? f : document.createTextNode(String(f)));
  }
  return n;
}

function icone(id, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("aria-hidden", "true");
  if (cls) svg.setAttribute("class", cls);
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
}

const num = (v) => v.toLocaleString("pt-BR");
const decimal = (v) => v.toFixed(1).replace(".", ",");
const distancia = (km) => (km < 1 ? `${Math.round(km * 1000)} m` : `${decimal(km)} km`);
const esperar = (ms) => new Promise((r) => setTimeout(r, ms));
const primeiroNome = () => ((estado.visitante && estado.visitante.nome) || "").split(" ")[0];

const guardar = {
  ler() { try { return localStorage.getItem(CHAVE); } catch { return null; } },
  gravar(v) { try { localStorage.setItem(CHAVE, v); } catch { /* navegação privada: só não lembra */ } },
  apagar() { try { localStorage.removeItem(CHAVE); } catch { /* idem */ } },
};

async function api(caminho, opcoes = {}) {
  const r = await fetch(caminho, { headers: { "Content-Type": "application/json" }, ...opcoes });
  let corpo = null;
  try { corpo = await r.json(); } catch { /* corpo vazio */ }
  if (!r.ok) {
    const msg = corpo && typeof corpo.detail === "string" ? corpo.detail : `Erro ${r.status}`;
    const e = new Error(msg); e.status = r.status; throw e;
  }
  return { status: r.status, corpo };
}

function transicao(fn) {
  if (document.startViewTransition && !MOVIMENTO_REDUZIDO) document.startViewTransition(fn);
  else fn();
}

/* ------------------------------------------------------------------ entrada: nome + CEP */

function mostrarEntrada() {
  clearInterval(estado.pollArea);
  transicao(() => {
    $("#app").hidden = true;
    $("#entrada-cep").hidden = false;
  });
  const v = estado.visitante;
  $("#nome").value = v ? v.nome || "" : "";
  $("#cep").value = v ? v.local.cep.replace(/^(\d{5})(\d{3})$/, "$1-$2") : "";
  $("#cep-status").textContent = "";
  ($("#nome").value ? $("#cep") : $("#nome")).focus({ preventScroll: true });
}

function mascaraCep(e) {
  const d = e.target.value.replace(/\D/g, "").slice(0, 8);
  e.target.value = d.length > 5 ? `${d.slice(0, 5)}-${d.slice(5)}` : d;
}

async function enviarCep(e) {
  e.preventDefault();
  const nome = $("#nome").value.trim();
  const digitos = $("#cep").value.replace(/\D/g, "");
  const status = $("#cep-status");
  status.className = "cep-status erro";
  if (!nome) { status.textContent = "Como posso te chamar? Coloca seu nome aí."; $("#nome").focus(); return; }
  if (digitos.length !== 8) { status.textContent = "O CEP tem 8 números. Confere aí?"; $("#cep").focus(); return; }
  status.className = "cep-status";
  status.replaceChildren(el("span", { class: "carregando" }, "Achando sua cidade…"));
  $("#btn-cep").disabled = true;
  try {
    const { corpo } = await api("/api/local", {
      method: "POST",
      body: JSON.stringify({ nome, cep: digitos, visitante_id: estado.visitante ? estado.visitante.visitante_id : null }),
    });
    guardar.gravar(corpo.visitante_id);
    estado.conversa = { id: null, itens: [] };
    abrirApp(corpo);
  } catch (err) {
    status.className = "cep-status erro";
    status.textContent = err.message || "Não consegui agora. Tenta de novo?";
  } finally {
    $("#btn-cep").disabled = false;
  }
}

/* ------------------------------------------------------------------ app */

function abrirApp(v) {
  estado.visitante = v;
  estado.contador = 0;
  transicao(() => {
    $("#entrada-cep").hidden = true;
    $("#app").hidden = false;
    $("#rotulo-local").textContent = v.local.rotulo;
    renderLateral();
    renderConversa();
  });
  acompanharArea();
}

function acompanharArea() {
  clearInterval(estado.pollArea);
  if (estado.visitante.area.estado === "pronta") return;
  estado.pollArea = setInterval(async () => {
    try {
      const { corpo } = await api(`/api/visitante/${estado.visitante.visitante_id}`);
      estado.visitante = corpo;
      renderMapa();
      if (corpo.area.estado === "pronta" || corpo.area.completo === false) clearInterval(estado.pollArea);
    } catch { /* tenta de novo no próximo ciclo */ }
  }, 3000);
}

function contarAte(alvo) {
  const box = $("#contador");
  const inicio = estado.contador;
  estado.contador = alvo;
  if (MOVIMENTO_REDUZIDO || alvo === inicio) { box.textContent = num(alvo); return; }
  const t0 = performance.now();
  const passo = (t) => {
    const p = Math.min(1, (t - t0) / 900);
    const eased = 1 - Math.pow(1 - p, 3);
    box.textContent = num(Math.round(inicio + (alvo - inicio) * eased));
    if (p < 1) requestAnimationFrame(passo);
  };
  requestAnimationFrame(passo);
}

function pontosRadar(n) {
  const box = $("#radar .pontos");
  const total = Math.min(n, 48);
  for (let i = box.children.length; i < total; i += 1) {
    const ang = i * 2.39996; // ângulo de ouro: espalha sem aglomerar
    const r = 12 + 30 * Math.sqrt((i + 1) / 48);
    box.append(el("i", { style: `left:${50 + r * Math.cos(ang)}%;top:${50 + r * Math.sin(ang)}%;animation-delay:${(i % 12) * 40}ms` }));
  }
}

function renderMapa() {
  const a = estado.visitante.area;
  const radar = $("#radar");
  radar.classList.toggle("buscando", a.estado !== "pronta" && a.completo !== false);
  const n = a.n_lugares || 0;
  pontosRadar(n);
  contarAte(n);
  $("#contador-texto").textContent = a.estado === "pronta" ? "lugares reais mapeados por perto"
    : a.estado === "parcial" ? (a.completo === false ? "lugares encontrados" : "lugares encontrados, buscando mais…")
      : a.estado === "erro" ? "o mapa não respondeu; tento de novo sozinho"
        : "procurando restaurantes por perto…";
}

function renderLateral() {
  const v = estado.visitante;
  $("#regiao-nome").textContent = v.local.bairro || v.local.cidade;
  $("#regiao-info").textContent = v.local.bairro ? `${v.local.cidade} - ${v.local.uf}` : v.local.uf;
  $("#radar .pontos").replaceChildren();
  $("#contador").textContent = "0";
  renderMapa();
  $("#rotulo-regionais").textContent = `típicos de ${v.local.uf}`;
  $("#regionais").replaceChildren(...v.regionais.map((r) =>
    el("button", { class: "chip", type: "button", title: r.descricao, onclick: () => enviar(`quero comer ${r.nome}`) }, r.nome)));
  renderMemoria();
}

function renderMemoria() {
  const mem = estado.visitante.memoria;
  const chips = [
    ...mem.restricao.map((x) => el("span", { class: "chip restricao" }, ROTULOS_MEMORIA[x] || x)),
    ...mem.evitar.map((x) => el("span", { class: "chip evitar" }, ROTULOS_MEMORIA[x] || x)),
    ...mem.gosto.map((x) => el("span", { class: "chip" }, x)),
  ];
  $("#memoria").replaceChildren(...(chips.length ? chips
    : [el("span", { class: "memoria-vazia" }, "Nada ainda. Conte suas restrições (\"sou vegano\") e eu lembro.")]));
}

async function recarregarVisitante() {
  try {
    const { corpo } = await api(`/api/visitante/${estado.visitante.visitante_id}`);
    estado.visitante = corpo;
    renderMemoria();
    renderMapa();
  } catch { /* segue com o que tem */ }
}

function boasVindas() {
  const v = estado.visitante;
  const nome = primeiroNome();
  const atalhos = [...ATALHOS.slice(0, 3), ...v.regionais.slice(0, 2).map((r) => [`📍 ${r.nome}`, `quero comer ${r.nome}`]),
    ATALHOS[4]];
  const hero = el("section", { class: "hero" },
    el("div", { class: "hero-emojis", "aria-hidden": "true" }, el("span", {}, "🍣"), el("span", {}, "🥗"), el("span", {}, "🌮")),
    el("span", { class: "hero-tag" }, nome ? `Oi, ${nome}! · 📍 ${v.local.rotulo}` : `📍 ${v.local.rotulo}`),
    el("h2", {}, "Bateu a fome.", el("em", {}, "Achou o prato.")),
    el("p", {}, "Me conta a vontade que eu sugiro pratos. Gostou de algum? Toque em “Onde comer perto” e eu mostro lugares reais por aqui."),
    el("div", { class: "atalhos" }, atalhos.map(([rotulo, texto], i) =>
      el("button", { class: "atalho", type: "button", style: `--i:${i}`, onclick: () => enviar(texto) }, rotulo))));
  const passos = el("div", { class: "como-funciona" },
    [["Busque.", "“algo leve”, “comida árabe”, “uma coisa típica daqui”."],
      ["Descubra.", "Pratos que combinam com você, com o porquê de cada um."],
      ["Escolha.", "“Onde comer perto” mostra lugares reais, distância e horário."]].map(([t, d], i) =>
      el("div", { class: "passo-card", style: `--i:${i}` }, el("span", { class: "num" }, `0${i + 1}`), el("b", {}, t), el("span", {}, d))));
  return [hero, passos];
}

function renderConversa() {
  const c = estado.conversa;
  const box = $("#mensagens");
  box.replaceChildren(...(c.itens.length ? c.itens.map(renderItem) : boasVindas()));
  // Resposta pronta: para no começo dela (pergunta + mensagem + primeiros cartões), não no fim da lista.
  const ultimo = c.itens[c.itens.length - 1];
  const pronta = ultimo && !["usuario", "digitando"].includes(ultimo.tipo) && !ultimo.carregando;
  const pergunta = pronta ? [...box.querySelectorAll(".msg.usuario")].pop() : null;
  const topo = !c.itens.length ? 0
    : pergunta ? box.scrollTop + pergunta.getBoundingClientRect().top - box.getBoundingClientRect().top - 12
      : box.scrollHeight;
  box.scrollTo({ top: topo, behavior: MOVIMENTO_REDUZIDO ? "auto" : "smooth" });
}

function autor(pensando = false) {
  return el("p", { class: `autor${pensando ? " pensando" : ""}` },
    el("span", { class: "avatar-lupa" }, icone("lupa")), "bora de quê?");
}

const esqueletos = (n = 3) => el("div", { class: "esqueletos", "aria-hidden": "true" },
  Array.from({ length: n }, () => el("div", { class: "esqueleto" }, el("i"), el("i"), el("i"), el("i"))));

function renderItem(item) {
  if (item.tipo === "usuario") return el("div", { class: "msg usuario" }, el("div", { class: "balao" }, item.texto));
  if (item.tipo === "digitando") {
    return el("div", { class: "msg bot", "aria-label": "Pensando" }, autor(true),
      el("p", { class: "legenda-espera" }, "Pensando no seu prato…"), esqueletos());
  }
  if (item.tipo === "erro") return el("div", { class: "msg bot" }, autor(), el("div", { class: "aviso" }, item.texto));
  if (item.tipo === "lugares") return renderLugaresDoPrato(item);
  const r = item.resposta;
  return el("div", { class: "msg bot" },
    autor(),
    el("div", { class: "balao" }, r.mensagem),
    r.aviso ? el("div", { class: "aviso" }, r.aviso) : null,
    r.pratos.length ? el("div", { class: "cartoes" }, r.pratos.map((p, i) => cartaoPrato(p, i))) : null,
    r.recomendacoes.length ? el("div", { class: "cartoes" }, r.recomendacoes.map((x, i) => cartaoLugar(x, i, 3))) : null,
    renderTrace(r));
}

function renderLugaresDoPrato(item) {
  const corpo = [];
  if (item.carregando) {
    corpo.push(el("p", { class: "legenda-espera" }, item.aguardandoMapa
      ? "Ainda estou mapeando os restaurantes da sua região (só na primeira vez). Já já aparecem…"
      : `Procurando onde comer ${item.prato.nome} perto de você…`), esqueletos());
    return el("div", { class: "msg bot" }, autor(true), ...corpo);
  }
  if (item.erro) {
    corpo.push(el("div", { class: "aviso" }, item.erro));
  } else if (!item.lugares.length) {
    corpo.push(el("div", { class: "balao" }, item.parcial
      ? `Nos primeiros lugares que encontrei não tem ${item.prato.nome}. Ainda estou completando o mapa da região; tente de novo em instantes.`
      : `Não achei lugares mapeados por perto que costumem servir ${item.prato.nome}. Que tal outro prato?`));
  } else {
    const rel = item.relaxados || [];
    const texto = rel.includes("cozinha")
      ? `Não achei casas de ${item.prato.cozinha} mapeadas por perto. Estes lugares de cozinha parecida podem ter ${item.prato.nome}. Vale perguntar!`
      : `Aqui perto, estes lugares costumam ter ${item.prato.nome}.${rel.includes("distancia") ? " Precisei procurar um pouco mais longe." : ""} Confira o cardápio antes de ir!`;
    corpo.push(el("div", { class: "balao" }, texto + (item.parcial
      ? " (Ainda estou completando o mapa da região: se pedir de novo daqui a pouco, podem aparecer mais opções.)" : "")));
    corpo.push(el("div", { class: "cartoes" }, item.lugares.map((x, i) => cartaoLugar(x, i, item.raio || 3))));
  }
  return el("div", { class: "msg bot" }, autor(), ...corpo);
}

function cartaoPrato(p, i = 0) {
  const [emoji, tom] = CATEGORIA[p.categoria] || ["🍽️", 0];
  const tags = (p.tags || []).filter((t) => ROTULO_TAG[t]).slice(0, 4).map((t) => el("span", { class: "chip" }, ROTULO_TAG[t]));
  return el("article", { class: "cartao prato", style: `--i:${i}` },
    el("div", { class: `capa t${tom}` },
      el("span", { class: "emoji", "aria-hidden": "true" }, emoji),
      el("span", { class: "etiqueta" }, p.cozinha),
      p.regional ? el("span", { class: "selo" }, `típico de ${p.regional}`) : null),
    el("div", { class: "corpo" },
      el("h4", {}, p.nome),
      el("div", { class: "descricao" }, p.descricao),
      tags.length ? el("div", { class: "tags" }, tags) : null,
      p.motivo ? el("div", { class: "motivo" }, p.motivo) : null,
      el("button", { class: "btn-primario", type: "button", onclick: () => ondeComer(p) }, "Onde comer perto", icone("pino", "pino"))));
}

function cartaoLugar(rec, i = 0, raio = 3) {
  const l = rec.lugar;
  const cat = (l.categorias || [])[0];
  const [emoji, tom] = CATEGORIA[cat] || ["🍽️", 0];
  const aberto = l.aberto_agora === true
    ? el("span", { class: "aberto sim" }, el("i", { class: "ponto-vivo" }), "aberto agora")
    : l.aberto_agora === false ? el("span", { class: "aberto nao" }, icone("relogio"), "fechado agora")
      : el("span", { class: "aberto" }, icone("relogio"), l.horario_hoje || "horário não informado");
  const dieta = Object.entries(l.dieta_status || {}).map(([r, s]) =>
    el("span", { class: `chip ${s === "confirmado" ? "confirmado" : "provavel"}` },
      s === "confirmado" ? `${ROTULO_DIETA[r]} ✓ confirmado` : `${ROTULO_DIETA[r]}: costuma ter`));
  const prato = rec.prato || l.prato;
  const evidencia = l.evidencia
    ? `${l.evidencia[0].toUpperCase()}${l.evidencia.slice(1)}. Confira o cardápio.`
    : "Típico desse tipo de cozinha. Confira o cardápio.";
  const pct = Math.max(6, Math.min(100, (l.distancia_km / Math.max(raio, l.distancia_km)) * 100));
  return el("article", { class: "cartao", style: `--i:${i}` },
    el("div", { class: `capa t${tom}` },
      el("span", { class: "emoji", "aria-hidden": "true" }, emoji),
      el("span", { class: "etiqueta" }, distancia(l.distancia_km)),
      aberto),
    el("div", { class: "corpo" },
      el("h4", {}, l.nome),
      el("div", { class: "cozinha" }, l.cozinha || "cozinha não informada no mapa",
        l.origem_categoria === "nome" ? el("small", {}, " · deduzido pelo nome") : null),
      l.endereco ? el("div", { class: "endereco" }, l.endereco) : null,
      el("div", { class: "distancia", title: "distância do seu CEP" },
        el("div", { class: "trilho" }, el("i", { style: `--p:${pct}%` })),
        el("small", {}, el("span", {}, "você"), el("b", {}, distancia(l.distancia_km)))),
      prato ? el("div", { class: "peca" }, el("b", {}, "Pra pedir"), el("span", {}, prato.nome), el("p", {}, evidencia)) : null,
      dieta.length ? el("div", { class: "dieta" }, dieta) : null,
      rec.motivo && rec.motivo !== l.evidencia ? el("div", { class: "motivo" }, rec.motivo) : null,
      el("div", { class: "links" },
        el("a", { href: l.mapa_url, target: "_blank", rel: "noopener" },
          el("span", { class: "btn-primario" }, "Ver no mapa", icone("externo"))),
        l.site ? el("a", { href: l.site.startsWith("http") ? l.site : `https://${l.site}`, target: "_blank", rel: "noopener nofollow" },
          el("span", { class: "btn-claro" }, "Site", icone("externo"))) : null),
      el("div", { class: "fonte" }, "dados: ", el("a", { href: l.osm_url, target: "_blank", rel: "noopener" }, "OpenStreetMap"))));
}

async function ondeComer(prato) {
  if (!estado.visitante) return;
  const c = estado.conversa;
  const item = { tipo: "lugares", prato, carregando: true, lugares: [] };
  c.itens.push({ tipo: "usuario", texto: `Onde comer ${prato.nome} perto de mim?` }, item);
  renderConversa();
  const inicio = Date.now();
  try {
    while (true) {
      const { status, corpo } = await api(`/api/onde-comer?visitante_id=${encodeURIComponent(estado.visitante.visitante_id)}&prato_id=${encodeURIComponent(prato.id)}`);
      if (status === 200) {
        Object.assign(item, { carregando: false, lugares: corpo.recomendacoes, relaxados: corpo.relaxados, raio: corpo.raio_km,
          parcial: corpo.area && corpo.area.estado === "parcial" });
        break;
      }
      if (corpo.area && corpo.area.estado === "erro") throw new Error("O mapa de restaurantes não respondeu agora. Tente de novo em instantes.");
      if (Date.now() - inicio > 120000) throw new Error("O mapa está demorando mais que o normal. Tente de novo daqui a pouco.");
      if (!item.aguardandoMapa) { item.aguardandoMapa = true; renderConversa(); }
      await esperar(2500);
    }
  } catch (e) {
    Object.assign(item, { carregando: false, erro: e.message || "Não consegui buscar os lugares agora." });
  }
  renderConversa();
}

function descreverPasso(p) {
  if (p.tipo === "llm") {
    const chamou = p.chamou && p.chamou.length ? ` → ${p.chamou.join(", ")}` : "";
    return ["llm", [el("span", { class: "rot" }, `LLM${chamou}`),
      ` · ${num(p.latencia_ms)} ms · ${num(p.tokens_entrada)} + ${num(p.tokens_saida)} tokens`]];
  }
  if (p.tipo === "ferramenta") {
    return ["ferramenta", [el("span", { class: "rot" }, p.nome), ` · ${num(p.latencia_ms)} ms`,
      el("code", {}, `args: ${JSON.stringify(p.args)}\n` + JSON.stringify(p.resultado, null, 1))]];
  }
  if (p.tipo === "grounding") {
    const o = { pediu_correcao: "ID fora das ferramentas: pedi correção", descartou: "ID inventado descartado",
                preco_inconsistente: "a mensagem citou preço" }[p.acao] || p.acao;
    return ["grounding", [el("span", { class: "rot" }, "trava de grounding"), ` · ${o}`,
      el("code", {}, JSON.stringify(p.ids_invalidos || p.valores))]];
  }
  if (p.tipo === "falha_llm") return ["grounding", [el("span", { class: "rot" }, "LLM indisponível"), ` · ${p.erro}`]];
  if (p.tipo === "limite_passos") return ["grounding", [el("span", { class: "rot" }, "limite de passos atingido")]];
  if (p.tipo === "modo_simples") return ["grounding", [el("span", { class: "rot" }, "modo simples"), " · pedi para reformular"]];
  return ["llm", [JSON.stringify(p)]];
}

function renderTrace(r) {
  const tokens = r.uso.tokens_entrada + r.uso.tokens_saida;
  const resumo = [r.passos.length === 1 ? "1 passo" : `${r.passos.length} passos`, `${decimal(r.latencia_ms / 1000)} s`,
    r.modo === "agente" ? `${num(tokens)} tokens` : "modo simples",
    r.custo_usd ? `US$ ${r.custo_usd.toFixed(4)}` : null].filter(Boolean).join(" · ");
  return el("details", { class: "trace" },
    el("summary", {}, `Como cheguei aqui · ${resumo}`),
    el("ol", { class: "passos" }, r.passos.map((p) => {
      const [classe, conteudo] = descreverPasso(p);
      return el("li", { class: classe }, ...conteudo);
    })));
}

async function enviar(texto) {
  texto = (texto || "").trim();
  if (!texto || estado.enviando || !estado.visitante) return;
  const c = estado.conversa;
  estado.enviando = true;
  $("#enviar").disabled = true;
  $("#entrada").value = "";
  autoAltura();
  c.itens.push({ tipo: "usuario", texto }, { tipo: "digitando" });
  renderConversa();
  try {
    const { corpo: r } = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ visitante_id: estado.visitante.visitante_id, mensagem: texto, conversa_id: c.id }),
    });
    c.id = r.conversa_id;
    c.itens.pop();
    c.itens.push({ tipo: "bot", resposta: r });
    if (r.area) { estado.visitante.area = r.area; renderMapa(); }
    if (r.passos.some((p) => p.nome === "salvar_preferencia")) recarregarVisitante();
  } catch (e) {
    c.itens.pop();
    if (e.status === 404) { guardar.apagar(); estado.visitante = null; mostrarEntrada(); return; }
    c.itens.push({ tipo: "erro", texto: e.message || "Não consegui falar com o servidor." });
  } finally {
    estado.enviando = false;
    $("#enviar").disabled = false;
    if (!$("#app").hidden) { renderConversa(); $("#entrada").focus({ preventScroll: true }); }
  }
}

function autoAltura() {
  const t = $("#entrada");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 140) + "px";
}

/* ------------------------------------------------------------------ microinterações */

// Inclinação 3D e brilho seguindo o ponteiro, delegado para todos os cartões.
function ativarInclinacao() {
  if (MOVIMENTO_REDUZIDO || !window.matchMedia("(hover: hover)").matches) return;
  const box = $("#mensagens");
  box.addEventListener("pointermove", (e) => {
    const c = e.target.closest(".cartao");
    if (!c) return;
    const r = c.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    c.style.setProperty("--ry", `${(x - 0.5) * 7}deg`);
    c.style.setProperty("--rx", `${(0.5 - y) * 7}deg`);
    c.style.setProperty("--mx", `${x * 100}%`);
    c.style.setProperty("--my", `${y * 100}%`);
  });
  box.addEventListener("pointerout", (e) => {
    const c = e.target.closest(".cartao");
    if (c && !c.contains(e.relatedTarget)) { c.style.setProperty("--rx", "0deg"); c.style.setProperty("--ry", "0deg"); }
  });
}

// Exemplos que se digitam sozinhos no campo vazio.
function exemplosNoCampo() {
  if (MOVIMENTO_REDUZIDO) return;
  const t = $("#entrada");
  const estreito = window.matchMedia("(max-width: 560px)");
  let i = 0, j = 0, apagando = false;
  const tick = () => {
    if (document.activeElement === t || t.value) { t.placeholder = "O que você quer comer?"; setTimeout(tick, 1200); return; }
    const alvo = EXEMPLOS[i];
    j += apagando ? -1 : 1;
    const prefixo = estreito.matches ? "" : "O que você quer comer? ";
    t.placeholder = `${prefixo}${alvo.slice(0, j)}`;
    if (!apagando && j === alvo.length) { apagando = true; setTimeout(tick, 1600); return; }
    if (apagando && j === 0) { apagando = false; i = (i + 1) % EXEMPLOS.length; }
    setTimeout(tick, apagando ? 28 : 55);
  };
  setTimeout(tick, 1500);
}

/* ------------------------------------------------------------------ início */

async function iniciar() {
  $("#form-cep").addEventListener("submit", enviarCep);
  $("#cep").addEventListener("input", mascaraCep);
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); enviar($("#entrada").value); });
  $("#entrada").addEventListener("input", autoAltura);
  $("#entrada").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviar($("#entrada").value); }
  });
  $("#trocar-local").addEventListener("click", mostrarEntrada);
  $("#nova").addEventListener("click", () => { estado.conversa = { id: null, itens: [] }; renderConversa(); });
  $("#esquecer").addEventListener("click", async () => {
    if (!estado.visitante) return;
    if (!confirm("Apagar seu nome, CEP, o que o assistente lembra e suas conversas?")) return;
    try { await api(`/api/visitante/${estado.visitante.visitante_id}`, { method: "DELETE" }); } catch { /* segue */ }
    guardar.apagar();
    estado.visitante = null;
    estado.conversa = { id: null, itens: [] };
    mostrarEntrada();
  });
  ativarInclinacao();
  exemplosNoCampo();

  api("/api/saude").then(({ corpo: s }) => {
    const st = $("#status");
    st.querySelector("b").textContent = s.llm ? `assistente on · ${s.llm}` : "modo simples";
    st.className = "status " + (s.llm ? "ok" : "simples");
  }).catch(() => { $("#status").querySelector("b").textContent = "servidor indisponível"; });

  const id = guardar.ler();
  if (id) {
    try {
      const { corpo } = await api(`/api/visitante/${id}`);
      abrirApp(corpo);
      return;
    } catch { guardar.apagar(); }
  }
  mostrarEntrada();
}

iniciar();
