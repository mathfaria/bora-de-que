-- bora de quê? — esquema (idempotente: pode rodar a cada subida do app)
create extension if not exists vector;

create or replace function haversine_km(lat1 double precision, lon1 double precision,
                                        lat2 double precision, lon2 double precision)
returns double precision language sql immutable parallel safe as $$
  select 2 * 6371.0 * asin(sqrt(
    power(sin(radians(lat2 - lat1) / 2), 2) +
    cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2), 2)
  ))
$$;

-- Cache de CEP -> endereço -> coordenadas (ViaCEP + Nominatim)
create table if not exists ceps (
  cep text primary key,
  logradouro text not null default '',
  bairro text not null default '',
  cidade text not null,
  uf text not null,
  ibge text not null default '',
  lat double precision not null,
  lon double precision not null,
  precisao text not null,
  criado_em timestamptz not null default now()
);

-- Regiões já baixadas do OpenStreetMap (para não consultar o Overpass de novo)
create table if not exists areas (
  id bigserial primary key,
  lat double precision not null,
  lon double precision not null,
  raio_m int not null,
  n_lugares int not null,
  fonte text not null,
  atualizado_em timestamptz not null default now()
);

-- Estabelecimentos reais (OpenStreetMap, © colaboradores do OSM, licença ODbL)
create table if not exists lugares (
  id text primary key,                      -- ex.: osm:n123456
  nome text not null,
  tipo text not null,                       -- amenity ou shop do OSM
  categorias text[] not null default '{}',  -- categorias do app
  rotulos text[] not null default '{}',     -- cozinha em português, como veio do OSM
  origem_categoria text not null,           -- osm | nome | tipo | nenhuma
  lat double precision not null,
  lon double precision not null,
  endereco text not null default '',
  horario text,                             -- opening_hours bruto
  site text,
  telefone text,
  dieta text[] not null default '{}',       -- confirmadas no OSM: vegetariano, vegano, sem_gluten, sem_lactose
  entrega boolean,
  tags jsonb not null default '{}'::jsonb,
  busca_texto text not null,
  busca tsvector generated always as (to_tsvector('portuguese'::regconfig, busca_texto)) stored,
  embedding vector(384),
  atualizado_em timestamptz not null default now()
);
create index if not exists lugares_coord_idx on lugares (lat, lon);
create index if not exists lugares_categorias_idx on lugares using gin (categorias);
create index if not exists lugares_busca_idx on lugares using gin (busca);
create index if not exists lugares_embedding_idx on lugares using hnsw (embedding vector_cosine_ops);

-- Quem usa o app: identificado por um id guardado no navegador. Só o CEP é lembrado.
create table if not exists visitantes (
  id uuid primary key,
  cep text references ceps(cep),
  criado_em timestamptz not null default now(),
  visto_em timestamptz not null default now()
);
alter table visitantes add column if not exists nome text not null default '';

create table if not exists preferencias (
  visitante_id uuid not null references visitantes(id) on delete cascade,
  tipo text not null check (tipo in ('restricao', 'evitar', 'gosto')),
  valor text not null,
  criado_em timestamptz not null default now(),
  primary key (visitante_id, tipo, valor)
);

create table if not exists conversas (
  id uuid primary key,
  visitante_id uuid not null references visitantes(id) on delete cascade,
  criado_em timestamptz not null default now()
);

create table if not exists mensagens (
  id bigserial primary key,
  conversa_id uuid not null references conversas(id) on delete cascade,
  papel text not null check (papel in ('user', 'assistant')),
  conteudo text not null,
  dados jsonb not null default '{}'::jsonb,
  criado_em timestamptz not null default now()
);
create index if not exists mensagens_conversa_idx on mensagens (conversa_id, id);

create table if not exists traces (
  id uuid primary key,
  conversa_id uuid references conversas(id) on delete cascade,
  visitante_id uuid,
  criado_em timestamptz not null default now(),
  modo text not null,
  latencia_ms int not null,
  tokens_entrada int not null default 0,
  tokens_saida int not null default 0,
  custo_usd double precision not null default 0,
  passos jsonb not null
);
