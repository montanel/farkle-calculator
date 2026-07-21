"""Start the Farkle optimiser web app.

    python run.py            # serve on :8000 and open a browser
    python run.py --port 9000 --no-browser
"""

import argparse

from farkle.server import main

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    main(port=args.port, open_browser=not args.no_browser)
