"""Entrypoint: wires the movie library, mpv, and the transports together.

Production ("ble") mode runs the BLE GATT service, the HTTP web service, and
an mDNS advertisement of that HTTP service concurrently: BLE is what the app
uses for control (and is a fallback for discovering the device's WiFi address
when mDNS multicast doesn't reach it), but BLE's tiny ATT payloads are a poor
fit for bulk data (movie library, thumbnails), so the app also discovers the
device directly over WiFi via mDNS and uses that for those. "http" mode
(dev/testing - no Bluetooth required, e.g. no BlueZ on Docker Desktop/macOS)
runs the HTTP service and mDNS advertisement alone, without BLE.

Also runs a background transcode worker (views/transcode_service.py) that
re-encodes movies, one at a time, to something this device's weak CPU can
actually decode smoothly - only while nothing is playing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import List

from .controllers.playback_controller import PlaybackController
from .models.library import MovieLibrary
from .models.player import MpvController
from .util import local_ip, sleep_unless_stopped

logger = logging.getLogger(__name__)

DEVICE_NAME = "MagicBoxieDevice"
MOVIES_DIR = Path(os.environ.get("MAGICBOXIE_MOVIES_DIR", "/movies"))
THUMBNAIL_DIR = Path(os.environ.get("MAGICBOXIE_THUMBNAIL_DIR", "/var/lib/magicboxie/thumbnails"))
TRANSCODE_DIR = Path(os.environ.get("MAGICBOXIE_TRANSCODE_DIR", "/var/lib/magicboxie/transcoded"))

# "ble" (default, real device) or "http" (dev/testing - no Bluetooth required,
# e.g. when there's no BlueZ available such as Docker Desktop on macOS).
TRANSPORT = os.environ.get("MAGICBOXIE_TRANSPORT", "ble")
HTTP_PORT = int(os.environ.get("MAGICBOXIE_HTTP_PORT", "8000"))

# bluez expires an advert after its Timeout elapses; re-registering periodically
# (well before that) keeps the device discoverable indefinitely.
ADVERT_TIMEOUT_SECONDS = 180
ADVERT_REFRESH_SECONDS = 150

# How long to wait before retrying BLE setup after it fails (e.g. BlueZ not
# having registered its adapter over D-Bus yet at boot).
BLE_RETRY_SECONDS = 5

# How long to wait before retrying mDNS registration after it fails (e.g. a
# stale record from a previous instance still cached on the network under
# the same name - see _run_mdns).
MDNS_RETRY_SECONDS = 5

# The home server (MagicBoxie-web) this device checks in with for new content
# when it has internet - see views/home_sync_service.py. Unset by default:
# the device works standalone (BLE/HTTP + whatever's already on disk), this
# is opportunistic on top of that, not a requirement.
HOME_SERVER_URL = os.environ.get("MAGICBOXIE_HOME_SERVER_URL", "")
HOME_SERVER_PASSWORD = os.environ.get("MAGICBOXIE_HOME_SERVER_PASSWORD", "")
# A minute, not longer: the device is meant to pick up newly-synced-enabled
# movies promptly whenever it happens to be on the home WiFi, and a failed
# check-in (server unreachable) is cheap and expected - see HomeServerSync.
HOME_SERVER_CHECKIN_SECONDS = int(os.environ.get("MAGICBOXIE_HOME_SERVER_CHECKIN_SECONDS", "60"))


def _mpv_output_args() -> List[str]:
    # Defaults target rendering straight to the framebuffer via DRM/KMS - the
    # Pi's own HDMI output, with no desktop environment running.
    # Override with MAGICBOXIE_MPV_ARGS if your hardware needs a different --vo/--gpu-context.
    raw = os.environ.get("MAGICBOXIE_MPV_ARGS", "--vo=gpu --gpu-context=drm")
    return raw.split()


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not MOVIES_DIR.is_dir():
        raise SystemExit(f"Movies directory does not exist: {MOVIES_DIR}")

    library = MovieLibrary(MOVIES_DIR, thumbnail_dir=THUMBNAIL_DIR, transcode_dir=TRANSCODE_DIR)

    player = MpvController(extra_args=_mpv_output_args())
    await player.start()

    controller = PlaybackController(library, player)
    # Resting state until something's selected to play - the screen should
    # never just be black/whatever mpv's own idle window looks like. Shows
    # empty for now; library.scan() runs concurrently below (_run_library_scan)
    # rather than blocking here, since probing a real movie library can take
    # minutes on a Pi Zero W and BLE/mDNS/HTTP registration shouldn't wait on it.
    await controller.show_idle_screen()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        tasks = [
            _run_http(controller, stop_event),
            _run_keyboard(controller, stop_event),
            _run_mdns(stop_event),
            _run_library_scan(controller, stop_event),
            _run_transcode(controller, stop_event),
            _run_idle_dim(controller, stop_event),
        ]
        if TRANSPORT != "http":
            tasks.append(_run_ble(controller, stop_event))
        if HOME_SERVER_URL:
            tasks.append(_run_home_sync(controller, stop_event))
        await asyncio.gather(*tasks)
    finally:
        await player.stop_process()


async def _run_http(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    from aiohttp import web

    from .views.web_service import create_app

    app = create_app(controller)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    logger.info("Serving MagicBoxie web API on port %d", HTTP_PORT)

    await stop_event.wait()
    await runner.cleanup()


async def _run_mdns(stop_event: asyncio.Event) -> None:
    """Advertises the HTTP API over mDNS/Bonjour so the app can find this
    device on the local WiFi network without needing a BLE connection first.

    Retries on any failure (e.g. zeroconf.NonUniqueNameException, which a
    fast restart loop can trigger if a previous instance's record hasn't
    expired from the network's cache yet) rather than letting it propagate
    up to the `asyncio.gather` in `_run()` and take down BLE/HTTP with it -
    mirrors _run_ble's identical rationale for the same asyncio.gather.
    """
    from .views.mdns_service import MdnsAdvertiser

    while not stop_event.is_set():
        advertiser = MdnsAdvertiser(DEVICE_NAME, local_ip(), HTTP_PORT)
        try:
            await advertiser.start()
        except Exception:
            logger.exception("mDNS advertising failed - retrying in %ds", MDNS_RETRY_SECONDS)
            await sleep_unless_stopped(stop_event, MDNS_RETRY_SECONDS)
            continue

        try:
            await stop_event.wait()
        finally:
            await advertiser.stop()


async def _run_library_scan(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    """Scans the movie library in a background thread (MovieLibrary.scan()
    is a blocking chain of ffprobe/ffmpeg subprocess calls) and refreshes
    the idle screen once it's done. One-shot, not retried on an interval -
    runs concurrently with BLE/mDNS/HTTP registration rather than before it,
    so the device is discoverable/controllable immediately instead of only
    after a scan that can take minutes for a real library.

    run_in_executor isn't interruptible by stop_event - a shutdown mid-scan
    (e.g. a redeploy restart) lets the scan run to completion in the
    background regardless, by which point systemd's SIGTERM may already
    have killed mpv too (KillMode=control-group sends it to the whole
    process group, mpv included). Checking stop_event before touching mpv
    again avoids writing to that now-dead connection and crashing the
    daemon on the way out.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, controller.library.scan)
    if not stop_event.is_set():
        await controller.show_idle_screen()


async def _run_transcode(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    """Re-encodes movies in the background, one at a time, to something this
    device's weak CPU can decode smoothly - see views/transcode_service.py
    for why and the encode settings. Only runs while idle (checked
    internally against controller.is_idle), so it never competes with
    playback decode for the same core."""
    from .views.transcode_service import TranscodeService

    await TranscodeService(controller).run(stop_event)


async def _run_idle_dim(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    """Dims the idle screen after extended inactivity - see
    views/idle_dim_service.py."""
    from .views.idle_dim_service import IdleDimService

    await IdleDimService(controller).run(stop_event)


async def _run_keyboard(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    """Escape-to-stop from a directly-attached USB keyboard - the device's
    only local input, independent of BLE/HTTP and the iOS app. Runs in both
    transport modes, and is a no-op if no keyboard is ever attached."""
    from .views.keyboard_service import KeyboardService

    await KeyboardService(controller).run(stop_event)


async def _run_ble(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    """Retries the whole BLE setup/advertise cycle on any failure, rather than
    letting one propagate up to the `asyncio.gather` in `_run()` and take
    down HTTP/mDNS with it - BLE is meant to be a resilient fallback control
    path, not a single-shot one, so a D-Bus hiccup or bluetoothd restarting
    should self-heal rather than take the whole daemon down with it.
    """
    while not stop_event.is_set():
        try:
            await _run_ble_once(controller, stop_event)
        except Exception:
            logger.exception("BLE service failed - retrying in %ds", BLE_RETRY_SECONDS)
        await sleep_unless_stopped(stop_event, BLE_RETRY_SECONDS)


async def _first_bluez_adapter(bus):
    """bluez_peripheral.util.Adapter.get_first()/get_all() assume every child
    node under /org/bluez is an adapter and blow up otherwise - but BlueZ
    also exposes non-adapter objects there on stock installs (e.g.
    /org/bluez/test, a built-in org.bluez.SimAccessTest1 debug object), which
    makes the upstream helper unusable on a real system. Do the same
    lookup ourselves, filtering to nodes that actually implement
    org.bluez.Adapter1.
    """
    from bluez_peripheral.util import Adapter

    root = await bus.introspect("org.bluez", "/org/bluez")
    for node in root.nodes:
        path = "/org/bluez/" + node.name
        introspection = await bus.introspect("org.bluez", path)
        if not any(interface.name == "org.bluez.Adapter1" for interface in introspection.interfaces):
            continue
        proxy = bus.get_proxy_object("org.bluez", path, introspection)
        return Adapter(proxy)
    raise RuntimeError("No Bluetooth adapter (org.bluez.Adapter1) found under /org/bluez")


# bluez_peripheral.advert.Advertisement.register() always exports itself at
# this fixed default path (it doesn't expose a way to override it from the
# call sites we use), and only unexports the local D-Bus object afterwards -
# it never tells BlueZ to unregister the advertisement. See
# _unregister_advertisement below.
_ADVERTISEMENT_PATH = "/com/spacecheese/bluez_peripheral/advert0"


async def _unregister_advertisement(adapter) -> None:
    """Tells BlueZ to drop whatever advertisement is currently registered at
    Advertisement's fixed default path, if any. Needed before every
    (re-)registration: bluez_peripheral's Advertisement.register() (see
    _ADVERTISEMENT_PATH) never does this itself, so registering a second
    Advertisement at the same path - which our periodic refresh loop below
    does every ADVERT_REFRESH_SECONDS - gets rejected by BlueZ with
    'Already Exists', permanently killing the advertisement after the first
    refresh. A no-op (DBusError swallowed) the first time through, when
    nothing's registered yet.
    """
    from dbus_next.errors import DBusError

    interface = adapter._proxy.get_interface("org.bluez.LEAdvertisingManager1")
    try:
        await interface.call_unregister_advertisement(_ADVERTISEMENT_PATH)
    except DBusError:
        pass


async def _run_ble_once(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    from bluez_peripheral.advert import Advertisement
    from bluez_peripheral.agent import NoIoAgent
    from bluez_peripheral.util import get_message_bus

    from .models import protocol
    from .views.ble_service import MagicBoxieService

    network_url = f"http://{local_ip()}:{HTTP_PORT}"
    service = MagicBoxieService(controller, network_url)

    bus = await get_message_bus()

    # Resolved ourselves (see _first_bluez_adapter) and passed in explicitly -
    # service.register()'s own default (adapter=None) falls back to bluez_peripheral's
    # buggy Adapter.get_first(), which would blow up here exactly as it does
    # for us below without this.
    adapter = await _first_bluez_adapter(bus)
    await service.register(bus, adapter=adapter)

    # Required for bluez to complete pairing requests; "no IO" since this
    # device has no screen/keyboard of its own to confirm a passkey with.
    agent = NoIoAgent()
    await agent.register(bus)

    await adapter.set_alias(DEVICE_NAME)

    service.start_status_polling()

    try:
        while not stop_event.is_set():
            # No local name here: legacy BLE advertising packets cap out at
            # 31 bytes, and our 128-bit custom service UUID alone already
            # takes 18 of those (plus 3 for the flags BlueZ adds
            # automatically) - there's no room left for a readable name too,
            # and bluez_peripheral's LocalName property is sent unconditionally
            # once set, so registration fails outright rather than silently
            # dropping it. Not a problem for the app: it scans by service
            # UUID (see MediaControlProtocol.serviceUUID), never by name.
            # adapter.set_alias() above still gives it a friendly name for
            # anything that connects and reads the Generic Access Profile.
            await _unregister_advertisement(adapter)
            advert = Advertisement("", [protocol.SERVICE_UUID], 0x0000, ADVERT_TIMEOUT_SECONDS)
            await advert.register(bus, adapter)
            logger.info("Advertising %r (WiFi: %s)", DEVICE_NAME, network_url)
            await sleep_unless_stopped(stop_event, ADVERT_REFRESH_SECONDS)
    finally:
        await service.stop_status_polling()


async def _run_home_sync(controller: PlaybackController, stop_event: asyncio.Event) -> None:
    """Retries a home server check-in on an interval. The device is offline
    most of the time, so a failed attempt (no route, DNS failure, etc.) is
    expected and just gets logged - not treated as fatal."""
    from .views.home_sync_service import HomeServerSync

    def set_syncing_title(title: str | None) -> None:
        controller.currently_syncing_movie_title = title

    sync = HomeServerSync(controller.library, HOME_SERVER_URL, HOME_SERVER_PASSWORD, on_progress=set_syncing_title)
    while not stop_event.is_set():
        try:
            await sync.check_in()
        except Exception:
            logger.exception("Home server check-in failed unexpectedly")
        await sleep_unless_stopped(stop_event, HOME_SERVER_CHECKIN_SECONDS)


def run() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    run()
