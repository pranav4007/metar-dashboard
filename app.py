import eventlet
eventlet.monkey_patch()

import os, time, sqlite3
import requests as req_lib
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO
from flask_cors import CORS

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "metar-secret-key")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

STATIONS      = ["VIAH", "VIDP", "VIBY", "VIAG", "VIND"]
DB_PATH       = os.environ.get("DB_PATH", "/tmp/metar_history.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
latest_metars = {}

# ── DB ────────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS metar_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        station TEXT NOT NULL, raw TEXT NOT NULL, fetched_at INTEGER NOT NULL
    )""")
    conn.commit(); conn.close()
    print("[DB] Ready", flush=True)

def save_metar(station, raw, ts):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT raw FROM metar_history WHERE station=? ORDER BY id DESC LIMIT 1", (station,)
    ).fetchone()
    changed = row is None or row[0] != raw
    if changed:
        conn.execute("INSERT INTO metar_history(station,raw,fetched_at) VALUES(?,?,?)", (station,raw,ts))
        conn.commit()
    conn.close()
    return changed

def get_history(station, limit=50):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT raw,fetched_at FROM metar_history WHERE station=? ORDER BY id DESC LIMIT ?", (station,limit)
    ).fetchall()
    conn.close()
    return [{"raw": r[0], "fetched_at": r[1]} for r in rows]

# ── METAR fetch — NOAA public API, no key needed ──────────────────────────────
# Primary:  https://aviationweather.gov/api/data/metar
# Fallback: https://tgftp.nws.noaa.gov/data/observations/metar/stations/

def fetch_metar(station):
    # --- Primary: NOAA API ---
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=raw&hours=3"
        r = req_lib.get(url, timeout=15)
        r.raise_for_status()
        text = r.text.strip()
        if text:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(station):
                    print(f"[NOAA-API] {station}: {line[:70]}", flush=True)
                    return line
            # if station id not in response, still return first non-empty line
            first = next((l.strip() for l in text.splitlines() if l.strip()), None)
            if first:
                print(f"[NOAA-API] {station} (raw): {first[:70]}", flush=True)
                return first
    except Exception as e:
        print(f"[NOAA-API] {station} failed: {e}", flush=True)

    # --- Fallback: NOAA text file ---
    try:
        url2 = f"https://tgftp.nws.noaa.gov/data/observations/metar/stations/{station}.TXT"
        r2 = req_lib.get(url2, timeout=15)
        r2.raise_for_status()
        lines = r2.text.strip().splitlines()
        # File format: first line = datetime, second = raw METAR
        for line in lines:
            line = line.strip()
            if line.startswith(station):
                print(f"[NOAA-TXT] {station}: {line[:70]}", flush=True)
                return line
    except Exception as e:
        print(f"[NOAA-TXT] {station} failed: {e}", flush=True)

    print(f"[ERROR] {station}: all sources failed", flush=True)
    return None

def fetch_and_emit(station):
    raw = fetch_metar(station)
    if raw:
        now = int(time.time())
        changed = save_metar(station, raw, now)
        latest_metars[station] = {"raw": raw, "fetched_at": now}
        socketio.emit("metar_update", {
            "station": station, "raw": raw,
            "fetched_at": now, "changed": changed
        })
        return True
    return False

def poll_loop():
    print("[POLL] Initial fetch starting...", flush=True)
    for s in STATIONS:
        fetch_and_emit(s)
        eventlet.sleep(1)
    print("[POLL] Initial fetch complete.", flush=True)

    while True:
        eventlet.sleep(POLL_INTERVAL)
        print("[POLL] Refresh cycle...", flush=True)
        for s in STATIONS:
            fetch_and_emit(s)
            eventlet.sleep(1)

# ── WebSocket — push snapshot to newly connected client ───────────────────────

@socketio.on("connect")
def on_connect():
    print(f"[WS] Client connected. Have {len(latest_metars)} stations cached.", flush=True)
    for s, d in latest_metars.items():
        socketio.emit("metar_update", {
            "station": s, "raw": d["raw"],
            "fetched_at": d["fetched_at"], "changed": False
        }, to=request.sid)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "stations": list(latest_metars.keys()), "ts": int(time.time())})

@app.route("/api/latest")
def api_latest():
    return jsonify(latest_metars)

@app.route("/api/latest/<station>")
def api_latest_station(station):
    d = latest_metars.get(station.upper())
    return jsonify(d) if d else (jsonify({"error": "not ready"}), 202)

@app.route("/api/history/<station>")
def api_history(station):
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(get_history(station.upper(), limit))

# Manual refresh trigger — hit /api/refresh in browser to force immediate fetch
@app.route("/api/refresh")
def api_refresh():
    def do_refresh():
        for s in STATIONS:
            fetch_and_emit(s)
            eventlet.sleep(0.5)
    socketio.start_background_task(do_refresh)
    return jsonify({"ok": True, "msg": "Refresh triggered"})

# ── Boot ──────────────────────────────────────────────────────────────────────

print("[BOOT] Starting...", flush=True)
init_db()
socketio.start_background_task(poll_loop)
print("[BOOT] Poll task queued.", flush=True)

port = int(os.environ.get("PORT", 10000))
socketio.run(app, host="0.0.0.0", port=port, log_output=True)
