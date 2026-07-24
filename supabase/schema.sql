-- Run this once in your Supabase project's SQL editor.
-- Enables email/password auth (already on by default) and creates the
-- history table with row-level security so each user only ever sees
-- (and can only insert/delete) their own generation history.

create table if not exists public.history (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  theme text not null,
  topic_count integer not null,
  content text not null,
  created_at timestamptz not null default now()
);

alter table public.history enable row level security;

create policy "Users can view their own history"
  on public.history for select
  using (auth.uid() = user_id);

create policy "Users can insert their own history"
  on public.history for insert
  with check (auth.uid() = user_id);

create policy "Users can delete their own history"
  on public.history for delete
  using (auth.uid() = user_id);

-- Default user_id to the logged-in user automatically on insert.
alter table public.history alter column user_id set default auth.uid();

create index if not exists history_user_id_created_at_idx
  on public.history (user_id, created_at desc);
