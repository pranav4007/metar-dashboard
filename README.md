# VIAH MET — Aviation Weather Dashboard

Real-time METAR dashboard for Aligarh (VIAH) and surrounding stations.

## Features
- 🕐 Live Local & UTC clocks
- 🌅 Sunrise / Sunset times for Aligarh
- 📡 Live METAR for VIAH (primary) + VIDP, VIBY, VIAG, VIND
- 🔔 Browser push notification + audio beep when VIAH METAR updates
- 📜 Full history per station (SQLite)
- ⚡ WebSocket live updates (no page refresh needed)
- 📱 Mobile responsive

## Local Dev

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Deploy to Render (Free Tier)

1. Push this folder to a **GitHub repo**

2. Go to https://render.com → New → Web Service

3. Connect your repo

4. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python app.py`
   - **Instance type:** Free

5. Add Environment Variables:
   | Key | Value |
   |-----|-------|
   | `SECRET_KEY` | (generate random string) |
   | `POLL_INTERVAL` | `300` (seconds between polls) |
   | `DB_PATH` | `/tmp/metar_history.db` |

6. Click **Create Web Service** → done!

> **Note:** Render free tier spins down after 15 min of inactivity. Use a cron ping service like [cron-job.org](https://cron-job.org) to hit `/api/health` every 10 minutes to keep it alive.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/latest` | Latest METAR for all stations |
| `GET /api/latest/VIAH` | Latest METAR for specific station |
| `GET /api/history/VIAH?limit=50` | History for station |
| `GET /api/health` | Health check |

## WebSocket Events

Connect to the root URL. Listen for `metar_update` event:
```json
{
  "station": "VIAH",
  "raw": "VIAH 091230Z ...",
  "fetched_at": 1712663400,
  "changed": true
}
```
