# Deploying MagicBoxie Player

Production runs directly on Raspberry Pi OS using systemd and a Python virtual
environment. Docker is for local development. Run the `pi-*` Make targets on
the Pi as the device account, with `sudo` access, from the project checkout.

## Device access and current installation

The development Mac's SSH alias `pi` resolves to `192.168.86.27`, hostname
`magicboxie`, account `admin`. The user identified this machine as the **home
server**, not the target device. Do not deploy device changes through that
alias until it has been pointed at the correct Pi.

On October 3, 2026, the device was verified over password-authenticated SSH:

| Item | Value |
| --- | --- |
| LAN address | `192.168.86.57` |
| SSH account | `admin` |
| Hardware | Raspberry Pi Zero 2 W Rev 1.0 |
| Hostname | `magicboxie-player` (`magicboxie-player.local`) |
| Checkout | `/home/admin/magicboxie-device` |
| Connection | Ethernet (`eth0`) |
| Daemon | `magicboxie-player.service`, active |
| Open ports checked | SSH 22 and device HTTP API 8000 |

Connect using `ssh admin@192.168.86.57` and the supplied device password.
Key authentication was not available. Replace `DEVICE_USER` in the commands
below with `admin` and `DEVICE_IP` with `192.168.86.57` (or its current address).
The API's `/api/version` endpoint reports that same LAN address. Port 80 was
closed, and the Wi-Fi startup and self-update units were not installed yet.
The OS detected an Apple Magic Keyboard with Numeric Keypad at
`/dev/input/event0`; the daemon logged that it was watching for Escape.

Look for the service `MagicBoxieDevice._magicboxie._tcp.local.`. The home
server also advertised that name with an unusable `127.0.0.1` address; neither
the name nor a matching API response alone identifies the target device.
Verify the responding host and hardware over SSH before deploying.

The home server had a running `magicboxie-player.service` on port 8000 and
nginx on port 80. Its Wi-Fi startup and self-update units were absent. Those
observations describe the home server, not the other Pi. The target's current service state is listed above.

Changes present only in a development working tree cannot reach the Pi through
`git pull`; commit and push the intended release before using Git deployment.

## Prerequisites

Use Raspberry Pi OS with NetworkManager, an AP-capable `wlan0`, and internet
access for Git, apt, and pip. Legacy dhcpcd/hostapd setups are not migrated by
the installer. Keep an Ethernet connection or local console available when
changing Wi-Fi configuration.

Check port ownership before deploying the portal:

```sh
sudo ss -ltnp 'sport = :80'
sudo ss -ltnp 'sport = :8000'
```

The new daemon listens on both ports 80 and 8000. If another service owns port 80, move or reconfigure that listener before
starting the new portal-enabled daemon; installation does not resolve this
conflict. Nginx was observed on the home server, so check the target separately.
The existing MagicBoxie daemon owning port 8000 is expected and is replaced
by restarting its service.

The unattended updater runs as the installing user and invokes `sudo` during
redeployment. Verify that account can run the required deployment commands
without a password prompt; interactive sudo access alone is insufficient.

## First installation or upgrading an older sparse checkout

On the Pi, run:

```sh
curl -fsSL https://raw.githubusercontent.com/kriogenx0/magicboxie-device/main/install.sh | sh
```

The bootstrap script defaults to branch `main` and `~/magicboxie-player`.
`MAGICBOXIE_REF` and `MAGICBOXIE_INSTALL_DIR` can override these defaults.
It installs Git if needed and creates a shallow sparse checkout containing
`pi/` (everything deployed to the device) and the root `Makefile`, which
forwards `pi-*` targets to `pi/Makefile`. It then runs `make pi-install`.
The service, virtualenv, and updater all run from `pi/` in the checkout.

On an existing checkout, bootstrap fetches the selected branch and resets
tracked files to the remote version. Preserve local edits before rerunning
it. Runtime movies and Wi-Fi credentials live outside the checkout.
Reusing the new bootstrap also expands older sparse checkouts to include
all `pi/system` files required by the new services.
If the daemon was already running, follow installation with `make pi-restart`:
`pi-install` uses `systemctl start`, which does not restart an active daemon.

```sh
cd ~/magicboxie-player
make pi-restart
```

For an already complete checkout of the intended revision, use:

```sh
cd ~/magicboxie-player
make pi-install
```

Installation runs these steps in order:

1. Install system dependencies, create/update `.venv`, and install the app.
2. Create content/cache directories and seed sample movies if `/content`
   contains no MP4 files. This can take time on a Pi Zero.
3. Render and enable the daemon and daily update timer.
4. Install the Wi-Fi startup script, open AP profile, and dedicated DNS/DHCP
   service; enable the startup policy.
5. Start the daemon, then queue the Wi-Fi startup check.

Wi-Fi selection is queued asynchronously; installer completion does not mean
that the 30-second window has finished. Inspect the startup logs to verify it.
Use these Make targets without `-j`, since installation order matters.

## Deploy a published update

After pushing the intended changes, connect to the Pi and run:

```sh
ssh DEVICE_USER@DEVICE_IP
cd ~/magicboxie-player
make pi
```

`make pi` pulls the current branch, refreshes packages and the virtual
environment, renders the service definitions, restarts the daemon, and queues
Wi-Fi selection. It restarts immediately, so schedule manual deployment when
playback can be interrupted.

To install an update through the playback-aware updater instead:

```sh
sudo systemctl start --no-block magicboxie-self-update.service
journalctl -u magicboxie-self-update -f
```

For code already present in the checkout, `make pi-redeploy` refreshes the
daemon and Wi-Fi installation and restarts them without pulling. It does not
install the update timer; use `make pi-install` or `make pi` when introducing
that service to an older device.

## Saved Wi-Fi and startup behavior

The tracked seed file is `pi/system/wifi-networks.json`. The installer copies it
to `/var/lib/magicboxie/wifi-networks.json` only if the runtime file is absent.
Existing runtime credentials survive installation, Git updates, and reboots.
The runtime file has mode `600` and belongs to the device account. The tracked
file may include credentials, as authorized for this project.

Edit an existing device's credentials with:

```sh
sudoedit /var/lib/magicboxie/wifi-networks.json
```

The format is:

```json
{
  "version": 1,
  "networks": [
    {"ssid": "Mitera", "password": "YOUR_MITERA_PASSWORD"},
    {"ssid": "AV-iPhone17Pro", "password": "YOUR_IPHONE_HOTSPOT_PASSWORD"}
  ]
}
```

Replace these placeholders with actual passwords. The tracked file currently
has an empty network list. An empty password means an open network, not an
unknown password. Successful BLE provisioning also saves credentials to the
runtime file. Profiles created manually with `nmcli` are not exported to JSON.

On boot, `magicboxie-wifi-startup.service` loads the JSON entries into stable
NetworkManager profiles, then allows up to 30 seconds for `wlan0` to connect.
Existing saved NetworkManager profiles with autoconnect enabled also work.

- Saved Wi-Fi connects: keep that connection and queue a self-update attempt.
  Wi-Fi does not need internet access to count as connected.
- No Wi-Fi connects within 30 seconds: activate **MagicBoxie Player**, with
  no password, at `10.42.0.1`. Ethernet alone does not suppress this fallback.
- Hotspot already active during deployment: preserve it and its clients.

The hotspot provides an offline captive portal at `http://10.42.0.1/` and the
existing API at `http://10.42.0.1:8000`. If the client does not open the login
page automatically, open the HTTP URL manually. Anyone within Wi-Fi range
can access the device's unauthenticated controls and API.

Startup chooses once; it does not continuously switch between saved Wi-Fi
and hotspot mode. BLE provisioning can switch the adapter to a supplied
network. To rerun startup selection, use `make pi-wifi-start` or reboot.
Editing the tracked seed does not replace an existing runtime file. Removing
an entry from JSON also does not delete its NetworkManager profile.

## Self-update lifecycle

The same `magicboxie-self-update.service` handles the request after saved
Wi-Fi connects and the daily timer. The timer uses a daily calendar schedule,
a randomized delay of up to one hour, and persistence for missed runs.

The updater:

1. Skips a checkout with local changes.
2. Pulls with `git pull --ff-only`. An offline or failed pull ends the attempt
   without disconnecting Wi-Fi; the next timer/startup request can retry.
3. Compares Git HEAD with `.git/magicboxie-installed-revision`. A missing or
   different marker requires installation, including retrying an interrupted
   deployment even if no new commits were pulled this time.
4. Publishes update status and waits until `/api/status` reports `stopped`.
   Polling occurs every 30 seconds. An unreachable API is not treated as idle.
5. Redeploys, restarts the daemon, and records the successfully installed
   revision. It clears the live update status when the updater exits.

The idle wait has no application timeout. A playing movie can delay
installation. Hotspot fallback does not request a startup update; the daily
timer remains enabled. Already running update requests use the same systemd
unit rather than launching parallel updater processes.

## Verification and operations

On the Pi:

```sh
systemctl status magicboxie-player magicboxie-wifi-startup
systemctl list-timers magicboxie-self-update.timer
nmcli -f NAME,TYPE,DEVICE connection show --active
curl -fsS http://localhost:8000/api/version
curl -fsS http://localhost:8000/api/status
curl -fsS http://localhost/
journalctl -u magicboxie-wifi-startup -b
journalctl -u magicboxie-self-update -n 100
```

`magicboxie-wifi-startup` normally becomes `active (exited)` after choosing a
network. `magicboxie-hotspot` should run only when AP mode is in use. The
self-update service can be inactive between attempts; check its logs and timer.

Use `make pi-logs` for live daemon logs, and `make pi-start`, `make pi-stop`,
or `make pi-restart` for routine service control. `make pi-uninstall` removes
the managed services and AP profile, while preserving movies and runtime
credentials. `make pi-clean` removes the virtual environment.

For discovery on macOS:

```sh
dns-sd -B _magicboxie._tcp local.
dns-sd -L MagicBoxieDevice _magicboxie._tcp local.
dns-sd -G v4v6 MagicBoxieDevice.local.
```

Stop these commands with Ctrl-C. Verify the advertised address is reachable;
`127.0.0.1` refers to the client itself. When discovery is wrong, use the
router's client list or a known LAN address, then confirm the systemd daemon
and its listener over SSH.

## Downloading movies from the home server

When the device can reach the home server (default `http://magicboxie.lan`,
for example after joining the Mitera Wi-Fi), it checks in every minute and
downloads movies it does not have yet, one at a time, while nothing is
playing. Downloads take priority over transcoding: a running transcode stops
when a download starts and resumes afterwards.

The server login is stored on the device in
`/var/lib/magicboxie/home-server.env` (mode `600`, outside Git), as
`MAGICBOXIE_HOME_SERVER_PASSWORD="..."`. Edit it with
`sudoedit /var/lib/magicboxie/home-server.env`, then run
`sudo systemctl restart magicboxie-player`. Updates and redeploys keep it. A
password passed once with `make pi-service HOME_SERVER_PASSWORD=...` is also
written there. Use `make pi-service HOME_SERVER_URL=...` to point at a
different server.
