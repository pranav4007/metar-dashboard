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

STATIONS    = ["VIAH", "VIDP", "VIBY", "VIAG", "VIND"]
DB_PATH     = os.environ.get("DB_PATH", "/tmp/metar_history.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))

latest_metars = {}
_started = False   # guard against double-init

# ── DB ────────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metar_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            station    TEXT    NOT NULL,
            raw        TEXT    NOT NULL,
            fetched_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_metar(station, raw, ts):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT raw FROM metar_history WHERE station=? ORDER BY id DESC LIMIT 1",
        (station,)
    ).fetchone()
    changed = row is None or row[0] != raw
    if changed:
        conn.execute(
            "INSERT INTO metar_history(station,raw,fetched_at) VALUES(?,?,?)",
            (station, raw, ts)
        )
        conn.commit()
    conn.close()
    return changed

def get_history(station, limit=50):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT raw,fetched_at FROM metar_history WHERE station=? ORDER BY id DESC LIMIT ?",
        (station, limit)
    ).fetchall()
    conn.close()
    return [{"raw": r[0], "fetched_at": r[1]} for r in rows]

# ── METAR ─────────────────────────────────────────────────────────────────────

def fetch_metar(station):
    try:
        m = avwx.Metar(station)
        m.update()
        return m.raw
    except Exception as e:
        print(f"[ERROR] {station}: {e}")
        return None

def poll_loop():
    print("[POLL] Starting initial fetch...")
    for s in STATIONS:
        raw = fetch_metar(s)
        if raw:
            now = int(time.time())
            save_metar(s, raw, now)
            latest_metars[s] = {"raw": raw, "fetched_at": now}
            socketio.emit("metar_update",
                {"station": s, "raw": raw, "fetched_at": now, "changed": True})
            print(f"[INIT] {s}: OK")
        eventlet.sleep(1)

    while True:
        eventlet.sleep(POLL_INTERVAL)
        print("[POLL] Polling all stations...")
        for station in STATIONS:
            raw = fetch_metar(station)
            if raw:
                now = int(time.time())
                changed = save_metar(station, raw, now)
                latest_metars[station] = {"raw": raw, "fetched_at": now}
                socketio.emit("metar_update",
                    {"station": station, "raw": raw, "fetched_at": now, "changed": changed})
                print(f"[POLL] {station}: {'CHANGED' if changed else 'same'}")
            eventlet.sleep(2)

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
    if d:
        return jsonify(d)
    return jsonify({"error": "not ready yet"}), 202

@app.route("/api/history/<station>")
def api_history(station):
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(get_history(station.upper(), limit))

# ── Bootstrap (runs once when module is first imported by any server) ─────────

def bootstrap():
    global _started
    if _started:
        return
    _started = True
    init_db()
    socketio.start_background_task(poll_loop)
    print("[BOOT] DB ready, poll task started.")

bootstrap()

# ── Dev runner ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[MAIN] Listening on 0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, log_output=True)
