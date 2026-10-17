-- Users, saved leagues, encrypted secrets and draft history.
-- Secrets tables have RLS enabled with no policies: only the backend (service role) can touch them.

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  birthdate date,
  claude_model text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.leagues (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  espn_league_id text not null check (espn_league_id ~ '^[0-9]+$'),
  season int not null,
  name text not null default '',
  team_id int,
  team_name text,
  is_default boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, espn_league_id, season)
);
create index leagues_user_idx on public.leagues (user_id);

create table public.league_secrets (
  league_id uuid primary key references public.leagues (id) on delete cascade,
  espn_s2_enc text,
  swid_enc text,
  updated_at timestamptz not null default now()
);

create table public.user_secrets (
  user_id uuid primary key references auth.users (id) on delete cascade,
  anthropic_key_enc text,
  updated_at timestamptz not null default now()
);

create table public.drafts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  league_id uuid references public.leagues (id) on delete set null,
  source text not null check (source in ('espn', 'sim')),
  config jsonb not null default '{}'::jsonb,
  picks jsonb not null default '[]'::jsonb,
  status text not null default 'active' check (status in ('active', 'complete', 'abandoned')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index drafts_user_idx on public.drafts (user_id, updated_at desc);

-- Row level security
alter table public.profiles enable row level security;
alter table public.leagues enable row level security;
alter table public.league_secrets enable row level security;
alter table public.user_secrets enable row level security;
alter table public.drafts enable row level security;

create policy "own profile read" on public.profiles for select to authenticated using ((select auth.uid()) = id);
create policy "own profile update" on public.profiles for update to authenticated
  using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

create policy "own leagues" on public.leagues for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

create policy "own drafts" on public.drafts for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

-- Belt and braces: no client role may read secrets even if a policy is added by mistake.
revoke all on public.league_secrets from anon, authenticated;
revoke all on public.user_secrets from anon, authenticated;

-- Profile row for every new auth user
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', split_part(new.email, '@', 1)));
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- updated_at maintenance
create function public.touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_touch before update on public.profiles for each row execute function public.touch_updated_at();
create trigger leagues_touch before update on public.leagues for each row execute function public.touch_updated_at();
create trigger drafts_touch before update on public.drafts for each row execute function public.touch_updated_at();
create trigger league_secrets_touch before update on public.league_secrets for each row execute function public.touch_updated_at();
create trigger user_secrets_touch before update on public.user_secrets for each row execute function public.touch_updated_at();
