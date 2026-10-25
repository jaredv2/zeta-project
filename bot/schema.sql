-- =====================================================================
-- ZETA bot schema for Supabase Postgres
-- Idempotent base schema. Run through KeyStore.ensure_schema() (API or bot),
-- which performs the required Python HMAC backfill before dropping legacy keys.
-- Do not apply this file manually to an existing database.
-- =====================================================================

-- Per-server settings written by /verify-setup ------------------------
create table if not exists public.zeta_guild_config (
    guild_id         bigint      primary key,
    verified_role_id bigint,
    channel_id       bigint,
    message_id       bigint,
    updated_at       timestamptz not null default now()
);

-- The keys themselves --------------------------------------------------
-- New installations store only a keyed HMAC verifier and a masked hint.
-- ensure_schema() upgrades legacy plaintext keys before dropping that column.
create table if not exists public.zeta_keys (
    id            bigint      generated always as identity primary key,
    key_hash      text        not null,
    key_hint      text        not null,
    owner_id      bigint      not null,              -- Discord user id
    guild_id      bigint,                            -- server it was issued in
    scope         text        not null default 'default',
    status        text        not null default 'active',
    note          text,
    created_by    bigint,                            -- owner (self-service) or admin id
    created_at    timestamptz not null default now(),
    revoked_at    timestamptz,
    revoked_by    bigint,
    revoke_reason text,
    constraint zeta_keys_scope_check      check (scope in ('all', 'default', 'builds', 'sweat', 'vip', 'pickaxes')),
    constraint zeta_keys_status_check     check (status in ('active', 'revoked'))
);

alter table public.zeta_keys add column if not exists key_hash text;
alter table public.zeta_keys add column if not exists key_hint text;

-- Add the pickaxe-only entitlement scope to databases created by older versions.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.zeta_keys'::regclass
          and conname = 'zeta_keys_scope_check'
          and pg_get_constraintdef(oid) like '%pickaxes%'
    ) then
        alter table public.zeta_keys drop constraint if exists zeta_keys_scope_check;
        alter table public.zeta_keys
            add constraint zeta_keys_scope_check
            check (scope in ('all', 'default', 'builds', 'sweat', 'vip', 'pickaxes'));
    end if;
end
$$;

-- A user can only ever hold ONE active key. The database enforces it,
-- so two simultaneous /get-key clicks cannot create two keys.
create unique index if not exists zeta_keys_one_active_per_owner
    on public.zeta_keys (owner_id)
    where status = 'active';

create index if not exists zeta_keys_owner_idx        on public.zeta_keys (owner_id, created_at desc);
create index if not exists zeta_keys_scope_status_idx on public.zeta_keys (scope, status);
create index if not exists zeta_keys_created_idx      on public.zeta_keys (created_at desc);

-- A single userscript installation may hold a key lease at a time. The lease
-- can be taken over after five minutes without an authenticated request.
create table if not exists public.zeta_key_sessions (
    key_id        bigint      primary key references public.zeta_keys (id) on delete cascade,
    client_hash   text        not null,
    last_seen_at  timestamptz not null default now()
);

-- Audit trail: who did what to which key -------------------------------
create table if not exists public.zeta_key_events (
    id         bigint      generated always as identity primary key,
    key_id     bigint      not null references public.zeta_keys (id) on delete cascade,
    action     text        not null,
    actor_id   bigint,
    details    jsonb       not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists zeta_key_events_key_idx on public.zeta_key_events (key_id, created_at desc);

-- Lobby background voting ----------------------------------------------
create table if not exists public.zeta_vote_config (
    guild_id          bigint      primary key,
    review_channel_id bigint      not null,
    updated_at        timestamptz not null default now()
);

create table if not exists public.zeta_votes (
    id                    bigint generated always as identity primary key,
    guild_id              bigint      not null,
    submission_channel_id bigint      not null,
    created_by            bigint      not null,
    status                text        not null default 'open',
    created_at            timestamptz not null default now(),
    closes_at             timestamptz not null,
    ended_at              timestamptz,
    constraint zeta_votes_status_check
        check (status in ('open', 'reviewing', 'selected', 'exhausted', 'closed'))
);

create unique index if not exists zeta_votes_one_active_per_guild
    on public.zeta_votes (guild_id)
    where status in ('open', 'reviewing');
create index if not exists zeta_votes_deadline_idx
    on public.zeta_votes (status, closes_at);

create table if not exists public.zeta_vote_submissions (
    id                bigint generated always as identity primary key,
    vote_id           bigint      not null references public.zeta_votes (id) on delete cascade,
    submitter_id      bigint      not null,
    message_id        bigint      unique,
    image_url         text,
    file_name         text,
    review_message_id bigint      unique,
    vote_count        integer     not null default 0,
    review_status     text        not null default 'posting',
    reviewed_by       bigint,
    reviewed_at       timestamptz,
    created_at        timestamptz not null default now(),
    constraint zeta_vote_submissions_status_check
        check (review_status in ('posting', 'pending', 'reviewing', 'rejected', 'selected', 'skipped')),
    constraint zeta_vote_submissions_vote_count_check check (vote_count >= 0)
);

create index if not exists zeta_vote_submissions_rank_idx
    on public.zeta_vote_submissions (vote_id, review_status, vote_count desc, created_at, id);
create index if not exists zeta_vote_submissions_owner_idx
    on public.zeta_vote_submissions (vote_id, submitter_id);

-- Security: Supabase exposes the public schema through its REST API.
-- Turning on Row Level Security with NO policies means the public
-- (anon / authenticated) API keys can read nothing here. The bot
-- connects as the database owner role, which bypasses RLS.
alter table public.zeta_guild_config enable row level security;
alter table public.zeta_keys         enable row level security;
alter table public.zeta_key_sessions enable row level security;
alter table public.zeta_key_events   enable row level security;
alter table public.zeta_vote_config enable row level security;
alter table public.zeta_votes enable row level security;
alter table public.zeta_vote_submissions enable row level security;

-- Debug output (visible in the "Messages" tab of the Supabase SQL editor)
do $$
begin
    raise notice '[DEBUG] ZETA schema ready: % keys, % events',
        (select count(*) from public.zeta_keys),
        (select count(*) from public.zeta_key_events);
end
$$;
