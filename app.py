import eventlet
eventlet.monkey_patch()

import os, time, sqlite3
import avwx
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

# ── METAR — your exact working approach, run in thread pool so it never blocks ─

def _avwx_fetch_blocking(station):
    """Runs in a real OS thread via tpool. Exactly your terminal script."""
    metar = avwx.Metar(station)
    metar.update()
    return metar.raw

def fetch_metar(station):
    """Called from eventlet greenlet — offloads blocking avwx call to thread."""
    try:
        # tpool.execute runs the function in a real thread,
        # yields control back to eventlet while waiting
        raw = eventlet.tpool.execute(_avwx_fetch_blocking, station)
        print(f"[FETCH] {station} RAW: {raw}", flush=True)
        return raw
    except Exception as e:
        print(f"[ERROR] {station}: {e}", flush=True)
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

@app.route("/api/refresh")
def api_refresh():
    """Hit this URL to force an immediate re-fetch of all stations."""
    def do_refresh():
        for s in STATIONS:
            fetch_and_emit(s)
            eventlet.sleep(1)
    socketio.start_background_task(do_refresh)
    return jsonify({"ok": True, "msg": "Refresh triggered for all stations"})

# ── Boot ──────────────────────────────────────────────────────────────────────

print("[BOOT] Starting...", flush=True)
init_db()
socketio.start_background_task(poll_loop)
print("[BOOT] Poll task queued.", flush=True)

port = int(os.environ.get("PORT", 10000))
socketio.run(app, host="0.0.0.0", port=port, log_output=True)
