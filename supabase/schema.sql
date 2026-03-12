-- DJ Track Pipeline — Supabase Schema
-- Run this in the Supabase SQL Editor (https://supabase.com/dashboard → SQL Editor)

-- ==========================================================================
-- tracks: every Spotify track the pipeline encounters
-- ==========================================================================
create table if not exists tracks (
  id              bigint generated always as identity primary key,
  spotify_id      text unique not null,
  track_name      text not null,
  artist_name     text not null default '',
  album_name      text not null default '',
  isrc            text not null default '',
  spotify_url     text not null default '',
  source_playlist text not null default '',

  -- Pipeline state: new → approved → carted → processing → done
  -- Also: skipped, cart_failed, baseline
  status          text not null default 'new'
    check (status in (
      'new', 'approved', 'carted', 'purchased', 'processing',
      'done', 'skipped', 'cart_failed', 'baseline'
    )),

  genre           text,

  -- Link resolver results (populated in Phase 3)
  beatport_url    text,
  traxsource_url  text,
  match_confidence text default 'not_found'
    check (match_confidence in ('high', 'medium', 'low', 'not_found')),
  confidence_score integer default 0,

  -- Apple Music playlists this track should be added to (user-selected)
  target_playlists jsonb not null default '[]'::jsonb,

  -- File pipeline metadata (populated in Phase 6)
  file_path       text,

  date_detected   timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- Auto-update updated_at on row changes
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger tracks_updated_at
  before update on tracks
  for each row
  execute function update_updated_at();

-- Indexes for common query patterns
create index if not exists idx_tracks_status on tracks (status);
create index if not exists idx_tracks_spotify_id on tracks (spotify_id);
create index if not exists idx_tracks_genre on tracks (genre);

-- ==========================================================================
-- playlist_snapshots: point-in-time record of a playlist's track IDs
-- ==========================================================================
create table if not exists playlist_snapshots (
  id              bigint generated always as identity primary key,
  playlist_id     text not null unique,
  playlist_name   text not null default '',
  track_ids       jsonb not null default '[]'::jsonb,
  snapshot_date   timestamptz not null default now()
);

create index if not exists idx_snapshots_playlist_id
  on playlist_snapshots (playlist_id);

-- ==========================================================================
-- Row Level Security (RLS) — allow full access via anon key for local app
-- ==========================================================================
alter table tracks enable row level security;
alter table playlist_snapshots enable row level security;

create policy "Allow all access to tracks"
  on tracks for all
  using (true)
  with check (true);

create policy "Allow all access to playlist_snapshots"
  on playlist_snapshots for all
  using (true)
  with check (true);
