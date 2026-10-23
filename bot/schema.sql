-- =====================================================================
-- ZETA bot schema for Supabase Postgres
-- Idempotent: safe to run many times (the bot also runs it on startup
-- when AUTO_MIGRATE=true). You can paste it into the Supabase SQL editor.
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
create table if not exists public.zeta_keys (
    id            bigint      generated always as identity primary key,
    key           text        not null,
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
    constraint zeta_keys_key_unique       unique (key),
    constraint zeta_keys_scope_check      check (scope in ('all', 'default', 'builds', 'sweat', 'vip')),
    constraint zeta_keys_status_check     check (status in ('active', 'revoked')),
    constraint zeta_keys_key_format_check check (key ~ '^ZETA(-[A-Z0-9]{4}){3}-ATEZ$')
);

-- A user can only ever hold ONE active key. The database enforces it,
-- so two simultaneous /get-key clicks cannot create two keys.
create unique index if not exists zeta_keys_one_active_per_owner
    on public.zeta_keys (owner_id)
    where status = 'active';

create index if not exists zeta_keys_owner_idx        on public.zeta_keys (owner_id, created_at desc);
create index if not exists zeta_keys_scope_status_idx on public.zeta_keys (scope, status);
create index if not exists zeta_keys_created_idx      on public.zeta_keys (created_at desc);

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

-- Security: Supabase exposes the public schema through its REST API.
-- Turning on Row Level Security with NO policies means the public
-- (anon / authenticated) API keys can read nothing here. The bot
-- connects as the database owner role, which bypasses RLS.
alter table public.zeta_guild_config enable row level security;
alter table public.zeta_keys         enable row level security;
alter table public.zeta_key_events   enable row level security;

-- Debug output (visible in the "Messages" tab of the Supabase SQL editor)
do $$
begin
    raise notice '[DEBUG] ZETA schema ready: % keys, % events',
        (select count(*) from public.zeta_keys),
        (select count(*) from public.zeta_key_events);
end
$$;
