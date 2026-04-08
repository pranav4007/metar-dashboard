import eventlet
eventlet.monkey_patch()

import os, time, sqlite3, traceback
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO
from flask_cors import CORS
import avwx

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "metar-secret-key")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

STATIONS      = ["VIAH", "VIDP", "VIBY", "VIAG", "VIND"]
DB_PATH       = os.environ.get("DB_PATH", "/tmp/metar_history.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
MAX_RETRIES   = 3
RETRY_DELAY   = 5
latest_metars = {}

# ── DB ────────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS metar_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        station TEXT NOT NULL, raw TEXT NOT NULL, fetched_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_station ON metar_history(station)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fetched ON metar_history(fetched_at)")
    conn.commit()
    conn.close()
    print("[DB] Ready", flush=True)

def save_metar(station, raw, ts):
    if not raw:
        return False
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

# ── METAR FETCHER (avwx with retries) ─────────────────────────────────────────

def fetch_metar_with_retry(station, retry_count=0):
    """Fetch METAR using avwx with retry logic"""
    try:
        print(f"[FETCH] {station}: Attempt {retry_count + 1}/{MAX_RETRIES + 1}", flush=True)
        
        # Create Metar object and update
        metar = avwx.Metar(station)
        metar.update()
        
        # Get the raw METAR string
        raw = metar.raw
        
        if raw:
            print(f"[FETCH] {station}: SUCCESS - {raw[:80]}", flush=True)
            return raw
        else:
            print(f"[FETCH] {station}: No data returned (raw is None/empty)", flush=True)
            if retry_count < MAX_RETRIES:
                print(f"[FETCH] {station}: Retrying in {RETRY_DELAY}s...", flush=True)
                eventlet.sleep(RETRY_DELAY)
                return fetch_metar_with_retry(station, retry_count + 1)
            return None
            
    except Exception as e:
        print(f"[FETCH] {station}: ERROR - {type(e).__name__}: {str(e)}", flush=True)
        traceback.print_exc()
        
        if retry_count < MAX_RETRIES:
            print(f"[FETCH] {station}: Retrying in {RETRY_DELAY}s...", flush=True)
            eventlet.sleep(RETRY_DELAY)
            return fetch_metar_with_retry(station, retry_count + 1)
        return None

def fetch_one(station):
    raw = fetch_metar_with_retry(station)
    now = int(time.time())
    
    if raw:
        changed = save_metar(station, raw, now)
        latest_metars[station] = {"raw": raw, "fetched_at": now}
        socketio.emit("metar_update", {
            "station": station, "raw": raw, "fetched_at": now,
            "changed": changed
        })
        status = "NEW" if changed else "same"
        print(f"[UPDATE] {station}: {status} - {raw[:60]}", flush=True)
    else:
        # Keep existing data if available, otherwise set error message
        if station in latest_metars:
            print(f"[UPDATE] {station}: Using cached data (fetch failed)", flush=True)
        else:
            latest_metars[station] = {"raw": f"METAR temporarily unavailable for {station}", "fetched_at": now}
            print(f"[UPDATE] {station}: No data available", flush=True)
    
    return raw

def poll_loop():
    # Warm up avwx service with a test request
    print("[POLL] Warming up avwx service...", flush=True)
    try:
        test = avwx.Metar("VIDP")
        test.update()
        print("[POLL] avwx service ready", flush=True)
    except Exception as e:
        print(f"[POLL] avwx warmup warning: {e}", flush=True)
    
    # Initial fetch for all stations
    print("[POLL] Initial fetch cycle starting...", flush=True)
    for s in STATIONS:
        fetch_one(s)
        eventlet.sleep(2)  # Small delay between stations to avoid rate limiting
    
    print(f"[POLL] Initial complete. {len(latest_metars)} stations loaded.", flush=True)

    # Continuous polling loop
    while True:
        eventlet.sleep(POLL_INTERVAL)
        print(f"[POLL] Scheduled refresh cycle at {time.strftime('%Y-%m-%d %H:%M:%S')} UTC...", flush=True)
        for s in STATIONS:
            fetch_one(s)
            eventlet.sleep(2)  # Delay between stations

# ── WebSocket ───────────────────────────────────────────────────────────────

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

@socketio.on("disconnect")
def on_disconnect():
    print("[WS] Client disconnected", flush=True)

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health")
def health():
    return jsonify({
        "ok": True, 
        "stations": list(latest_metars.keys()), 
        "ts": int(time.time()),
        "station_count": len(latest_metars),
        "avwx_available": True
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

@app.route("/api/test/<station>")
def test_fetch(station):
    """Test endpoint to manually fetch a station"""
    station = station.upper()
    raw = fetch_metar_with_retry(station)
    return jsonify({
        "station": station,
        "raw": raw,
        "success": raw is not None,
        "timestamp": int(time.time())
    })

# ── Boot ──────────────────────────────────────────────────────────────────

print("[BOOT] Starting VIAH METAR Dashboard...", flush=True)
print(f"[BOOT] Stations to monitor: {STATIONS}", flush=True)
print(f"[BOOT] Poll interval: {POLL_INTERVAL}s", flush=True)
print(f"[BOOT] DB path: {DB_PATH}", flush=True)

init_db()

# Start background polling
socketio.start_background_task(poll_loop)
print("[BOOT] Poll task started successfully", flush=True)

port = int(os.environ.get("PORT", 10000))
print(f"[BOOT] Starting server on port {port}", flush=True)

socketio.run(app, host="0.0.0.0", port=port, log_output=True)
