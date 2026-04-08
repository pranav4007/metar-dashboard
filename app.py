import eventlet
eventlet.monkey_patch()

import os, time, sqlite3
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO
from flask_cors import CORS
import avwx

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["71cad267b678677801d1499b3dd10f5e"] = os.environ.get("71cad267b678677801d1499b3dd10f5e", "71cad267b678677801d1499b3dd10f5e")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

STATIONS = ["VIAH", "VIDP", "VIBY", "VIAG", "VIND"]
DB_PATH = os.environ.get("DB_PATH", "/tmp/metar_history.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
latest_metars = {}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS metar_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        station TEXT NOT NULL, 
        raw TEXT NOT NULL, 
        fetched_at INTEGER NOT NULL
    )""")
    conn.commit()
    conn.close()
    print("[DB] Ready", flush=True)

def save_metar(station, raw, ts):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT raw FROM metar_history WHERE station=? ORDER BY id DESC LIMIT 1", (station,)
    ).fetchone()
    changed = row is None or row[0] != raw
    if changed:
        conn.execute("INSERT INTO metar_history(station,raw,fetched_at) VALUES(?,?,?)", 
                    (station, raw, ts))
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

def fetch_one(station):
    try:
        print(f"[FETCH] Starting {station}...", flush=True)
        m = avwx.Metar(station)
        m.update()  # This might take a few seconds
        raw = m.raw
        
        if raw:
            now = int(time.time())
            changed = save_metar(station, raw, now)
            latest_metars[station] = {"raw": raw, "fetched_at": now}
            socketio.emit("metar_update", {
                "station": station, 
                "raw": raw, 
                "fetched_at": now,
                "changed": changed
            })
            print(f"[FETCH] ✅ {station}: {raw[:80]}", flush=True)
            return raw
        else:
            print(f"[FETCH] ❌ {station}: No data returned", flush=True)
            return None
            
    except Exception as e:
        print(f"[ERROR] {station}: {e}", flush=True)
        return None

def poll_loop():
    print("[POLL] Starting initial fetch...", flush=True)
    
    # Initial fetch - one station at a time with timeout protection
    for s in STATIONS:
        print(f"[POLL] Fetching {s}...", flush=True)
        fetch_one(s)
        eventlet.sleep(3)  # Give each station time to complete
    
    print("[POLL] Initial fetch complete!", flush=True)
    
    # Continuous polling loop
    while True:
        eventlet.sleep(POLL_INTERVAL)
        print(f"[POLL] Starting refresh cycle at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
        
        for s in STATIONS:
            fetch_one(s)
            eventlet.sleep(2)  # Small delay between stations

@socketio.on("connect")
def on_connect():
    print(f"[WS] Client connected, pushing {len(latest_metars)} stations", flush=True)
    for s, d in latest_metars.items():
        socketio.emit("metar_update", {
            "station": s, 
            "raw": d["raw"], 
            "fetched_at": d["fetched_at"], 
            "changed": False
        }, to=request.sid)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health")
def health():
    return jsonify({
        "ok": True, 
        "stations": list(latest_metars.keys()), 
        "ts": int(time.time()),
        "count": len(latest_metars)
    })

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

# Start the application
print("[BOOT] Starting up...", flush=True)
init_db()

# Start the background poll loop
socketio.start_background_task(poll_loop)
print("[BOOT] Poll task started.", flush=True)

port = int(os.environ.get("PORT", 10000))
print(f"[BOOT] Starting server on port {port}", flush=True)
socketio.run(app, host="0.0.0.0", port=port, log_output=True, debug=False)
