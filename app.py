from flask import Flask, render_template, jsonify
import threading
import json
from fetcher import update_loop

app = Flask(__name__)

# Start background thread
threading.Thread(target=update_loop, daemon=True).start()


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/current")
def current():
    try:
        with open("metar_store.json", "r") as f:
            history = json.load(f)
            return jsonify(history[-1])
    except:
        return jsonify({})


@app.route("/history")
def history():
    try:
        with open("metar_store.json", "r") as f:
            return jsonify(json.load(f))
    except:
        return jsonify([])


if __name__ == "__main__":
    app.run(debug=True)
