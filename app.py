import eventlet
eventlet.monkey_patch()

import os
import time
import sqlite3
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO
from flask_cors import CORS
import avwx

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "metar-secret-key")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

STATIONS = ["VIAH", "VIDP", "VIBY", "VIAG", "VIND"]
DB_PATH = os.environ.get("DB_PATH", "/tmp/metar_history.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))  # 5 minutes

# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS metar_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            station    TEXT    NOT NULL,
            raw        TEXT    NOT NULL,
            fetched_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_metar(station, raw, fetched_at):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT raw FROM metar_history WHERE station=? ORDER BY id DESC LIMIT 1", (station,))
    row = c.fetchone()
    changed = row is None or row[0] != raw
    if changed:
        c.execute("INSERT INTO metar_history (station, raw, fetched_at) VALUES (?,?,?)", (station, raw, fetched_at))
        conn.commit()
    conn.close()
    return changed

def get_history(station, limit=50):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT raw, fetched_at FROM metar_history WHERE station=? ORDER BY id DESC LIMIT ?", (station, limit))
    rows = c.fetchall()
    conn.close()
    return [{"raw": r[0], "fetched_at": r[1]} for r in rows]

# ── METAR fetching ────────────────────────────────────────────────────────────

latest_metars = {}

def fetch_metar(station):
    try:
        m = avwx.Metar(station)
        m.update()
        return m.raw
    except Exception as e:
        print(f"[ERROR] {station}: {e}")
        return None

def poll_loop():
    while True:
        for station in STATIONS:
            raw = fetch_metar(station)
            now = int(time.time())
            if raw:
                changed = save_metar(station, raw, now)
                latest_metars[station] = {"raw": raw, "fetched_at": now}
                socketio.emit("metar_update", {"station": station, "raw": raw, "fetched_at": now, "changed": changed})
                print(f"[POLL] {station}: {'NEW' if changed else 'same'}")
            eventlet.sleep(2)
        print(f"[POLL] Cycle done. Sleeping {POLL_INTERVAL}s")
        eventlet.sleep(POLL_INTERVAL)

# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/latest")
def api_latest():
    return jsonify(latest_metars)

@app.route("/api/latest/<station>")
def api_latest_station(station):
    station = station.upper()
    if station in latest_metars:
        return jsonify(latest_metars[station])
    raw = fetch_metar(station)
    if raw:
        now = int(time.time())
        save_metar(station, raw, now)
        latest_metars[station] = {"raw": raw, "fetched_at": now}
        return jsonify(latest_metars[station])
    return jsonify({"error": "Could not fetch METAR"}), 503

@app.route("/api/history/<station>")
def api_history(station):
    station = station.upper()
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(get_history(station, limit))

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "timestamp": int(time.time())})

@app.route("/")
def index():
    return render_template("index.html")

# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("[STARTUP] Initial METAR fetch...")
    for s in STATIONS:
        raw = fetch_metar(s)
        if raw:
            now = int(time.time())
            save_metar(s, raw, now)
            latest_metars[s] = {"raw": raw, "fetched_at": now}
            print(f"[STARTUP] {s}: OK")

    socketio.start_background_task(poll_loop)
    port = int(os.environ.get("PORT", 5000))
    print(f"[STARTUP] Server on :{port}")
    socketio.run(app, host="0.0.0.0", port=port)
