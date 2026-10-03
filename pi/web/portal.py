"""Offline landing page for clients connected to the device hotspot."""
from pathlib import Path

PORTAL_URL = "http://10.42.0.1/"

STATIC_DIR = Path(__file__).parent / "static"  # style.css, app.js

# The page lives in index.html beside this module; read once at import.
PAGE = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
