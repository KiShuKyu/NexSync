-- NexSync — Supabase SQL Schema
-- Run this in: supabase.com → your project → SQL Editor → New Query
-- Then click RUN

-- ── Enable UUID generation ────────────────────────────────────────────────────
create extension if not exists "pgcrypto";


-- ── users ─────────────────────────────────────────────────────────────────────
-- Mirrors Supabase auth.users — one row per NexSync account
create table if not exists public.users (
  id         uuid primary key references auth.users(id) on delete cascade,
  email      text not null,
  created_at timestamptz default now()
);

-- Auto-create user row when someone signs up
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.users (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();


-- ── devices ───────────────────────────────────────────────────────────────────
-- Each machine running NexSync
create table if not exists public.devices (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  hostname    text not null,
  machine_id  text not null unique,   -- stable UUID per machine, never changes
  platform    text not null,          -- "windows" / "darwin" / "linux"
  local_ip    text,
  sync_folder text,
  last_seen   timestamptz default now(),
  is_online   boolean default false,
  created_at  timestamptz default now()
);

create index if not exists devices_user_id_idx     on public.devices(user_id);
create index if not exists devices_machine_id_idx  on public.devices(machine_id);
create index if not exists devices_is_online_idx   on public.devices(is_online);


-- ── pairs ─────────────────────────────────────────────────────────────────────
-- Two machines that are paired together
create table if not exists public.pairs (
  id          uuid primary key default gen_random_uuid(),
  device_1_id uuid not null references public.devices(id) on delete cascade,
  device_2_id uuid not null references public.devices(id) on delete cascade,
  paired_at   timestamptz default now(),
  unique(device_1_id, device_2_id)
);

create index if not exists pairs_device_1_idx on public.pairs(device_1_id);
create index if not exists pairs_device_2_idx on public.pairs(device_2_id);


-- ── queue ─────────────────────────────────────────────────────────────────────
-- Files queued to send — metadata only, actual file lives locally
create table if not exists public.queue (
  id          uuid primary key default gen_random_uuid(),
  sender_id   uuid not null references public.devices(id) on delete cascade,
  receiver_id uuid not null references public.devices(id) on delete cascade,
  filename    text not null,
  file_size   bigint default 0,
  caption     text default '',
  status      text not null default 'pending',  -- pending / confirmed / sent
  queued_at   timestamptz default now(),
  constraint  queue_status_check check (status in ('pending', 'confirmed', 'sent'))
);

create index if not exists queue_receiver_idx on public.queue(receiver_id);
create index if not exists queue_status_idx   on public.queue(status);


-- ── sync_log ──────────────────────────────────────────────────────────────────
-- History of every sync event from all machines
create table if not exists public.sync_log (
  id        uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.devices(id) on delete cascade,
  action    text not null,    -- "push" / "pull" / "share" / "conflict"
  filename  text default '',
  timestamp timestamptz default now(),
  constraint sync_log_action_check check (action in ('push', 'pull', 'share', 'conflict'))
);

create index if not exists sync_log_device_idx   on public.sync_log(device_id);
create index if not exists sync_log_timestamp_idx on public.sync_log(timestamp desc);


-- ── Row Level Security ────────────────────────────────────────────────────────
-- Users can only see their own data

alter table public.users   enable row level security;
alter table public.devices enable row level security;
alter table public.pairs   enable row level security;
alter table public.queue   enable row level security;
alter table public.sync_log enable row level security;

-- users: only see your own row
create policy "users: own row only"
  on public.users for all
  using (auth.uid() = id);

-- devices: only see devices belonging to your account
create policy "devices: own devices only"
  on public.devices for all
  using (auth.uid() = user_id);

-- pairs: only see pairs where one device is yours
create policy "pairs: own pairs only"
  on public.pairs for all
  using (
    device_1_id in (select id from public.devices where user_id = auth.uid())
    or
    device_2_id in (select id from public.devices where user_id = auth.uid())
  );

-- queue: only see queue items you sent or are receiving
create policy "queue: sender or receiver only"
  on public.queue for all
  using (
    sender_id   in (select id from public.devices where user_id = auth.uid())
    or
    receiver_id in (select id from public.devices where user_id = auth.uid())
  );

-- sync_log: only see logs from your devices
create policy "sync_log: own devices only"
  on public.sync_log for all
  using (
    device_id in (select id from public.devices where user_id = auth.uid())
  );


-- ── Enable Realtime ───────────────────────────────────────────────────────────
-- After running this schema, also do this manually in Supabase dashboard:
-- Database → Replication → enable Realtime for: devices, queue
--
-- Or run these (may require superuser in some Supabase tiers):
alter publication supabase_realtime add table public.devices;
alter publication supabase_realtime add table public.queue;
