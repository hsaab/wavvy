-- Manual file processing: downloaded status + download_path
-- Run once in Supabase SQL Editor if your tracks table already exists.

alter table tracks add column if not exists download_path text;

alter table tracks drop constraint if exists tracks_status_check;

alter table tracks add constraint tracks_status_check
  check (status in (
    'new', 'approved', 'carted', 'purchased', 'downloaded', 'processing',
    'done', 'skipped', 'cart_failed', 'baseline'
  ));
