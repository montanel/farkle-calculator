"""Local web server for the Farkle optimiser.

Stdlib only -- no pip installs. Solving runs on a background thread and the
page polls for progress, so a long calculation never blocks the browser.
"""

import json
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .dice import (DEFAULT_DIE, DICE, DICE_DESC, DICE_ICON, DICE_PROBS,
                   DICE_WEIGHTS, DICE_WIKI, WILDCARD)
from .solver import (DEFAULT_TARGET, NEED_TARGETS, default_workers, groups,
                     optimize, warm_up)

WEB_ROOT = Path(__file__).parent / "web"
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript",
                 ".css": "text/css", ".png": "image/png", ".jpg": "image/jpeg",
                 ".svg": "image/svg+xml", ".woff2": "font/woff2"}

_jobs = {}
_lock = threading.Lock()


def _run_job(job_id, inventory, refine, target):
    def progress(stage, done, total):
        with _lock:
            _jobs[job_id].update(stage=stage, done=done, total=total)

    try:
        result = optimize(inventory, DICE_PROBS, refine=refine,
                          progress=progress, fill_die=DEFAULT_DIE, target=target)
        with _lock:
            _jobs[job_id].update(stage="done", result=result)
    except Exception as exc:  # surface failures in the UI rather than the console
        with _lock:
            _jobs[job_id].update(stage="error", error=f"{type(exc).__name__}: {exc}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console clean

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/dice":
            out = []
            for name in sorted(DICE):
                weights, total, _, _ = DICE[name]
                out.append({
                    "name": name,
                    "icon": DICE_ICON.get(name),
                    "desc": DICE_DESC.get(name, ""),
                    "weights": weights,
                    "total": total,
                    "probs": DICE_PROBS.get(name),
                    "wiki": DICE_WIKI.get(name),
                    "wildcard": WILDCARD.get(name),
                })
            return self._send(200, {"dice": out, "fill_die": DEFAULT_DIE})

        if path == "/api/job":
            job_id = self.path.split("id=")[-1] if "id=" in self.path else ""
            with _lock:
                job = _jobs.get(job_id)
            if job is None:
                return self._send(404, {"error": "unknown job"})
            return self._send(200, job)

        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            return self._send(404, "not found", "text/plain")
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        return self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        if self.path != "/api/optimize":
            return self._send(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or "{}")
        raw = payload.get("inventory", {})
        refine = max(1, min(int(payload.get("refine", 20)), 60))
        # The game-points target the sets are ranked for; only standard targets solve.
        try:
            target = int(payload.get("target", DEFAULT_TARGET))
        except (TypeError, ValueError):
            target = DEFAULT_TARGET
        if target not in NEED_TARGETS:
            target = DEFAULT_TARGET

        # {name: count}; a bare list of names is also accepted.
        if isinstance(raw, list):
            inventory = {}
            for name in raw:
                inventory[name] = inventory.get(name, 0) + 1
        else:
            inventory = {k: int(v) for k, v in raw.items() if int(v) > 0}

        unknown = sorted(n for n in inventory if n not in DICE_PROBS)
        if unknown:
            blocked = [n for n in unknown if n in WILDCARD]
            if blocked:
                return self._send(400, {"error":
                    f"{', '.join(blocked)}: wildcard dice are not supported"})
            return self._send(400, {"error": f"unknown dice: {', '.join(unknown)}"})

        job_id = uuid.uuid4().hex[:12]
        with _lock:
            _jobs[job_id] = {"stage": "queued", "done": 0, "total": 1}
        threading.Thread(target=_run_job, args=(job_id, inventory, refine, target),
                         daemon=True).start()
        return self._send(200, {"job": job_id})


def main(port=8000, open_browser=True):
    print("Building rule tables...", end=" ", flush=True)
    groups()
    print("done.")
    # Start the solver processes now rather than mid-request: spawning them is
    # several seconds the user would otherwise spend watching a stalled bar.
    print(f"Starting {default_workers()} solver processes...", end=" ", flush=True)
    warm_up()
    print("done.")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Farkle optimiser running at {url}   (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
