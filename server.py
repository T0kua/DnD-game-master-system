"""Flask player server.

Routes:
  /                 landing with a list of personal character links
  /p/<token>        personal character sheet for one player
  /map              fullscreen map projection (second screen / TV)
  /api/state        full snapshot (used by /map and the landing page)
  /api/player/<t>   one character's payload (used by /p/<t>)
  /api/events       Server-Sent Events for instant updates
  /media/<file>     uploaded images / audio / video
"""

import json
import os

from flask import Flask, Response, jsonify, render_template, send_from_directory

from game_state import GameState

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(UPLOADS_DIR, exist_ok=True)


def create_app(state: GameState) -> Flask:
    app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/map")
    def map_view():
        return render_template("map.html")

    @app.route("/p/<token>")
    def player_sheet(token):
        return render_template("player.html", token=token)

    @app.route("/api/state")
    def api_state():
        return jsonify(state.snapshot())

    @app.route("/api/player/<token>")
    def api_player(token):
        payload = state.player_payload(token)
        if payload is None:
            return jsonify({"error": "not_found"}), 404
        return jsonify(payload)

    @app.route("/api/events")
    def api_events():
        def stream():
            last = -1
            while True:
                with state.cond:
                    state.cond.wait(timeout=15.0)
                    rev = state.revision
                if rev != last:
                    last = rev
                    yield "data: %s\n\n" % json.dumps({"revision": rev})
                else:
                    yield ": keep-alive\n\n"
        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    @app.route("/media/<path:filename>")
    def media(filename):
        return send_from_directory(UPLOADS_DIR, filename)

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    return app


def start_server(state: GameState, host="0.0.0.0", port=5000):
    app = create_app(state)
    app.run(host=host, port=port, threaded=True, use_reloader=False)
    return app
