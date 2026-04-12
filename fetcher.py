import avwx
import time
import json
from datetime import datetime

STATIONS = ["VIAH", "VIDP", "VIAG", "VIBY", "VIND"]

def fetch_metars():
    data = {}

    for station in STATIONS:
        try:
            m = avwx.Metar(station)
            m.update()

            data[station] = {
                "raw": m.raw,
                "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            }

        except Exception as e:
            data[station] = {
                "raw": f"ERROR: {str(e)}",
                "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            }

    return data


def update_loop():
    while True:
        print("UPDATING METARS...")
        metars = fetch_metars()

        try:
            with open("metar_store.json", "r") as f:
                history = json.load(f)
        except:
            history = []

        history.append({
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "data": metars
        })

        # Keep last 100 records
        history = history[-100:]

        with open("metar_store.json", "w") as f:
            json.dump(history, f, indent=2)

        time.sleep(1800)  # 30 minutes
