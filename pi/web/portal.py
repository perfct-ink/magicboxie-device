"""Offline landing page for clients connected to the device hotspot."""
from pathlib import Path

PORTAL_URL = "http://10.42.0.1/"

STATIC_DIR = Path(__file__).parent / "static"  # style.css, app.js

# The page lives in index.html beside this module; read once at import.
PAGE = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

# Shown inside the OS sign-in popup (reached by the probe redirects): it only
# tells the user to open PORTAL_URL in their real browser.
WELCOME_URL = PORTAL_URL + "welcome"
WELCOME_PAGE = (Path(__file__).parent / "welcome.html").read_text(encoding="utf-8")
