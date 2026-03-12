# Wavvy

Local Mac application that automates DJ track acquisition — from Spotify playlist monitoring through purchase to iTunes library organization.

## Prerequisites

- macOS (Catalina+)
- Python 3.11+
- Node.js 18+
- A [Supabase](https://supabase.com) project (free tier)
- Spotify Developer app credentials

## Setup

### 1. Clone and configure

```bash
cp .env.example .env
# Edit .env with your Beatport/Traxsource credentials

cp config.example.json config.json
# Edit config.json with your Spotify + Supabase credentials
```

### 2. Supabase schema

Run this SQL in your Supabase SQL editor:

```sql
CREATE TABLE tracks (
    id BIGSERIAL PRIMARY KEY,
    spotify_id TEXT UNIQUE NOT NULL,
    track_name TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    album_name TEXT,
    isrc TEXT,
    spotify_url TEXT,
    source_playlist TEXT,
    beatport_url TEXT,
    traxsource_url TEXT,
    match_confidence TEXT DEFAULT 'none',
    confidence_score INTEGER DEFAULT 0,
    genre TEXT,
    status TEXT DEFAULT 'new',
    skip_reason TEXT,
    local_file_path TEXT,
    date_detected TIMESTAMPTZ DEFAULT NOW(),
    date_completed TIMESTAMPTZ
);

CREATE INDEX idx_tracks_status ON tracks(status);
CREATE INDEX idx_tracks_spotify_id ON tracks(spotify_id);

CREATE TABLE playlist_snapshots (
    id BIGSERIAL PRIMARY KEY,
    playlist_id TEXT NOT NULL,
    playlist_name TEXT,
    track_ids JSONB NOT NULL,
    snapshot_date TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_snapshots_playlist ON playlist_snapshots(playlist_id);
```

### 3. Install dependencies

```bash
# Backend
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Frontend
cd ../frontend
npm install
```

### 4. Run the app

From the project root, start both servers with one command:

```bash
./start.sh
```

This launches the backend (port 8000) and frontend (port 5173) together. Press `Ctrl+C` to stop both.

The frontend runs at `http://localhost:5173` and proxies API calls to the backend at `http://localhost:8000`.

<details>
<summary>Run servers individually</summary>

```bash
# Backend
cd backend && source venv/bin/activate
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm run dev
```

</details>

## Architecture

- **Backend**: Python / FastAPI (port 8000)
- **Frontend**: React / Vite / Tailwind (port 5173)
- **Database**: Supabase (remote Postgres)
- **Real-time**: WebSocket at `/ws`
