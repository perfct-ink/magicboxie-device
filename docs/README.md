# MagicBoxie Device

The MagicBoxie device daemon runs on a Raspberry Pi, plays video over HDMI,
and exposes BLE and local HTTP control surfaces. Production installs run
natively under systemd; Docker is only used for development.

## Install on a Raspberry Pi

Start with Raspberry Pi OS (or another Debian-based Pi installation), connect
the Pi to the internet, and open a terminal on it directly or over SSH. The
installing user must have `sudo` access.

Run the bootstrap installer:

```sh
curl -fsSL https://raw.githubusercontent.com/kriogenx0/magicboxie-device/main/install.sh | sh
```

The script:

- installs Git if necessary;
- creates a minimal checkout in `~/magicboxie-device`;
- installs the required system and Python packages;
- creates `/content` for movies and seeds it with sample videos when empty;
- installs and enables the `magicboxie-device` systemd service; and
- starts the service immediately.

The install may take a while on a Pi Zero because it installs packages and
generates the sample videos.

### Verify the installation

Check the service:

```sh
systemctl status magicboxie-device
```

Follow its logs:

```sh
cd ~/magicboxie-device
make pi-logs
```

The local HTTP API listens on port 8000. Confirm it is responding from the Pi:

```sh
curl http://localhost:8000/api/version
```

Press `Ctrl-C` to stop following logs; this does not stop the service.

## Add movies

Place supported video files in `/content`, then ask the daemon to rescan:

```sh
cp "My Movie.mp4" /content/
curl -X POST http://localhost:8000/api/rescan
```

The service generates thumbnails and creates device-optimized transcodes in
the background while playback is idle. Original movies remain in `/content`.

## Update the device

From the checkout on the Pi, pull the latest code, refresh dependencies and
the service definition, and restart:

```sh
cd ~/magicboxie-device
make pi
```

## Service commands

Run these from `~/magicboxie-device`:

```sh
make pi-start
make pi-stop
make pi-restart
make pi-logs
```

To rerun the complete installer safely, use the original bootstrap command.
It detects the existing checkout, updates it, and reapplies the installation.

## Troubleshooting

- Confirm the daemon is running with `systemctl status magicboxie-device`.
- Inspect recent logs with `journalctl -u magicboxie-device -n 100`.
- Confirm Bluetooth is available with `bluetoothctl show`.
- Confirm the API locally with `curl http://localhost:8000/api/version`.
- Reboot after the initial installation if you plan to run the daemon manually;
  the installer changes the user's `video`, `input`, and `bluetooth` groups.

## Local development

With Docker installed, run:

```sh
make dev
```

This builds the development image, seeds sample movies when needed, and starts
the HTTP transport. Run the test suite with `make test`.

Startup and recovery:

- Startup lists movies without running ffprobe or ffmpeg. Durations are cached by file size and modification time; missing metadata and thumbnails are filled in after playback stops.
- Downloads, uploads, thumbnails, metadata, and playback state use temporary files and atomic replacement. Completed writes are synced to disk. Startup removes abandoned partial files.
- Playback failures quarantine the rejected file with a `.corrupt` suffix. A failed optimized copy falls back to the original; a failed original is excluded so another movie can play. Quarantined files remain available for inspection.
- Each movie keeps its own resume position, checkpointed every five seconds and saved immediately on Stop and movie changes. Natural completion clears that movie's position. Startup still chooses randomly and honors input received during startup.
- HTTP `update_status` and BLE characteristic `...000000000009` report a UTF-8 update message (empty BLE value / null HTTP field when inactive). Software updates wait for the current movie to finish and suppress autoplay until installation ends. The iOS app displays this over either transport.
- The updater records the successfully installed revision separately from Git HEAD, so an interrupted installation is retried. Its live status belongs to the updater process and expires rather than leaving a permanent updating indication after a crash.

Background scheduling:

- Player/HDMI initialization overlaps partial-file recovery and the fast library scan. Initial screen rendering runs alongside control-service startup.
- Screen rendering, durable playback saves, upload/download writes, metadata writes, and completed-file publication run in worker threads. Writes still finish durably before the corresponding operation succeeds; cancellation waits for an active disk write before cleanup.
- Home-server registration continues during playback. Large downloads and transcoding remain idle-only to protect playback on the Pi Zero's single core.
- Pending background tasks are cancelled and joined on shutdown. A completed background screen render cannot replace a movie selected while it was rendering.
