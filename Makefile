IMAGE := magicboxie-device
MOVIES_DIR := movies
VENV := .venv
THUMBNAIL_DIR := /var/lib/magicboxie/thumbnails
TRANSCODE_DIR := /var/lib/magicboxie/transcoded
# MagicBoxie-web's LAN hostname - see player_app/views/home_sync_service.py.
# Override (e.g. to empty) for a device with no home server to sync with.
HOME_SERVER_URL := http://magicboxie.local
# Deliberately no default (and never commit a real one here) - pass on the
# command line at deploy time: make pi-service HOME_SERVER_PASSWORD=...
HOME_SERVER_PASSWORD :=
# Fixed absolute path on the Pi where content lives - independent of wherever
# this repo happens to be checked out, unlike MOVIES_DIR above (which is
# Docker-dev-only, relative to the repo, and unrelated to the real device).
CONTENT_DIR := /content
# Matches MAGICBOXIE_HTTP_PORT's own default (see main.py) - only used here
# to poll the device's own /api/status from pi-wait-until-idle below, so
# override this too if you've overridden that.
HTTP_PORT := 8000
SERVICE_NAME := magicboxie-device
SERVICE_FILE := /etc/systemd/system/$(SERVICE_NAME).service
SELF_UPDATE_NAME := magicboxie-self-update
SELF_UPDATE_SERVICE_FILE := /etc/systemd/system/$(SELF_UPDATE_NAME).service
SELF_UPDATE_TIMER_FILE := /etc/systemd/system/$(SELF_UPDATE_NAME).timer
BOOT_UPDATE_NAME := magicboxie-boot-update
BOOT_UPDATE_SERVICE_FILE := /etc/systemd/system/$(BOOT_UPDATE_NAME).service

.PHONY: all setup dev build test clean seed-movies \
	pi pi-pull pi-install pi-setup pi-seed-movies pi-run pi-test pi-service pi-start pi-stop \
	pi-restart pi-redeploy pi-wait-until-idle pi-self-update pi-self-update-service pi-logs \
	pi-uninstall pi-clean pi-wifi-service pi-wifi-start

all: dev

# --- Docker dev (Mac/other non-Pi machines) -------------------------------
# These targets build/run in Docker for local development off the Pi. For
# the real device, use the `pi-*` targets below instead - the Pi runs the
# daemon natively (systemd + a venv), since Docker buys nothing on a
# single-purpose device and adds overhead the Pi Zero W can't spare.

# Everything here builds/runs in Docker - no local Python/venv needed.
setup:
	@command -v docker >/dev/null || { echo "docker not found - install Docker Desktop / Engine first"; exit 1; }
	docker build --target base -t $(IMAGE):latest .

# Seeds a few sample videos into an empty movies/ dir so there's always
# something to browse in dev - real movie files are large and won't be
# sitting in a fresh checkout. No-ops if movies/ already has content, so
# it's safe to depend on from `dev` every time rather than only once.
# Durations are chosen to land in each of the three MovieCategory buckets.
seed-movies:
	@mkdir -p $(MOVIES_DIR)
	@if [ -z "$$(find $(MOVIES_DIR) -maxdepth 1 -iname '*.mp4' -print -quit)" ]; then \
		echo "No movies found - seeding sample videos into $(MOVIES_DIR)/..."; \
		docker build --target base -t $(IMAGE):latest . >/dev/null; \
		docker run --rm -v "$$(pwd)/$(MOVIES_DIR):/movies" --entrypoint sh $(IMAGE):latest -c '\
			ffmpeg -loglevel error -f lavfi -i "testsrc=duration=6000:size=320x240:rate=1" -y "/movies/Sample Feature Film.mp4" && \
			ffmpeg -loglevel error -f lavfi -i "smptebars=duration=1500:size=320x240:rate=1" -y "/movies/Sample TV Episode.mp4" && \
			ffmpeg -loglevel error -f lavfi -i "testsrc=duration=180:size=320x240:rate=1" -y "/movies/Sample Clip.mp4" \
		'; \
	else \
		echo "Movies already present in $(MOVIES_DIR)/, skipping seed."; \
	fi

# Build for development and run it. `docker compose up --build` recreates the
# container from the freshly built image automatically, so there's nothing to
# manually delete between runs.
dev: seed-movies
	docker compose up --build

# Production image build.
build:
	docker build --target base -t $(IMAGE):latest .

# Runs the test suite inside the same image the app ships in.
test:
	docker build --target test -t $(IMAGE):test .
	docker run --rm $(IMAGE):test

clean:
	docker compose down --rmi local --volumes --remove-orphans 2>/dev/null || true
	docker image rm -f $(IMAGE):latest $(IMAGE):test 2>/dev/null || true

# --- Raspberry Pi (native, no Docker) -------------------------------------
# The real device: installs system packages + a venv directly on the Pi and
# runs the daemon as a systemd service (BLE transport, DRM/KMS HDMI output).
# Run these on the Pi itself, over SSH or a directly attached keyboard.

# One-shot: after copying this directory onto the Pi, `make pi-install` is
# the single command that gets a running, boot-persistent device - installs
# packages, seeds sample movies into /content if it's empty, installs+enables
# the systemd service (plus its daily self-update timer), and starts it.
# (The service itself gets video/input/bluetooth access straight from its
# unit file's SupplementaryGroups, so it doesn't need the installing shell's
# own group membership to have refreshed - that only matters if you
# separately use `make pi-run`.)
pi-install: pi-setup pi-seed-movies pi-service pi-self-update-service pi-wifi-service pi-start pi-wifi-start
	@echo "pi-install complete - MagicBoxie is running and will start automatically on boot."
	@echo "Check status with: make pi-logs"

# Day-to-day version of pi-install, for after the device is already set up:
# pulls whatever's new, re-runs setup (covers newly-added system deps or a
# changed pyproject.toml - a no-op otherwise), re-renders the systemd units
# (covers changes to either deploy/*.in template), and restarts. Every step
# is idempotent, so this is safe to re-run any time you've pushed changes
# and want the Pi caught up and running them.
pi: pi-pull pi-setup pi-service pi-self-update-service pi-wifi-service
	sudo systemctl restart $(SERVICE_NAME)
	$(MAKE) pi-wifi-start
	@echo "Pi is set up, deployed, and running - check status with: make pi-logs"

# Devices installed from install.sh use a sparse checkout; refresh its patterns
# so deploy files added since the original install are checked out.
pi-sparse-refresh:
	@if [ "$$(git config --get core.sparseCheckout)" = "true" ]; then \
		git sparse-checkout set --no-cone player_app pi deploy pyproject.toml Makefile; \
	fi

pi-pull: pi-sparse-refresh
	git pull

# System packages (mpv/ffmpeg/bluez + build headers for evdev/Pillow, curl
# for pi-wait-until-idle's polling below) and a venv with the app installed.
# Adds the invoking user to the video/input/bluetooth groups it needs for
# DRM output, keyboard Escape-to-stop, and BLE - re-login (or reboot) is
# required for that group change to apply.
pi-setup: pi-sparse-refresh
	@command -v apt-get >/dev/null || { echo "apt-get not found - pi-* targets are for Raspberry Pi OS/Debian"; exit 1; }
	sudo apt-get update
	sudo apt-get install -y --no-install-recommends \
		python3-venv python3-dev build-essential \
		mpv ffmpeg libjpeg-dev zlib1g-dev \
		fonts-dejavu-core \
		bluez dbus \
		curl network-manager dnsmasq-base avahi-daemon
	@if [ "$$(hostname)" != "magicboxie-player" ]; then \
		echo "Setting hostname to magicboxie-player (reachable as magicboxie-player.local via Avahi)..."; \
		sudo hostnamectl set-hostname magicboxie-player; \
	fi
	sudo systemctl enable --now avahi-daemon
	sudo usermod -aG video,input,bluetooth "$$(whoami)"
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e .
	sudo mkdir -p $(THUMBNAIL_DIR) $(TRANSCODE_DIR) $(CONTENT_DIR)
	sudo chown "$$(whoami)" $(THUMBNAIL_DIR) $(TRANSCODE_DIR) $(CONTENT_DIR)
	sudo install -d -m 700 -o "$$(whoami)" /var/lib/magicboxie
	@if [ ! -e /var/lib/magicboxie/wifi-networks.json ]; then \
		sudo install -m 600 -o "$$(whoami)" deploy/wifi-networks.json /var/lib/magicboxie/wifi-networks.json; \
	fi
	sudo chown "$$(whoami)" /var/lib/magicboxie/wifi-networks.json
	sudo chmod 600 /var/lib/magicboxie/wifi-networks.json
	@echo "pi-setup complete - log out/in (or reboot) so the new group membership takes effect."

# Same sample-video seeding as `seed-movies`, but into the real device's
# fixed content directory, using the ffmpeg installed straight onto the Pi
# by pi-setup instead of a Docker image.
pi-seed-movies:
	@if [ -z "$$(find $(CONTENT_DIR) -maxdepth 1 -iname '*.mp4' -print -quit 2>/dev/null)" ]; then \
		echo "No movies found - seeding sample videos into $(CONTENT_DIR)/..."; \
		ffmpeg -loglevel error -f lavfi -i "testsrc=duration=6000:size=320x240:rate=1" -y "$(CONTENT_DIR)/Sample Feature Film.mp4" && \
		ffmpeg -loglevel error -f lavfi -i "smptebars=duration=1500:size=320x240:rate=1" -y "$(CONTENT_DIR)/Sample TV Episode.mp4" && \
		ffmpeg -loglevel error -f lavfi -i "testsrc=duration=180:size=320x240:rate=1" -y "$(CONTENT_DIR)/Sample Clip.mp4"; \
	else \
		echo "Movies already present in $(CONTENT_DIR)/, skipping seed."; \
	fi

# Foreground run in the current terminal - useful for a quick check or
# debugging without installing the systemd service. Ctrl-C to stop.
pi-run: pi-seed-movies
	MAGICBOXIE_MOVIES_DIR=$(CONTENT_DIR) MAGICBOXIE_THUMBNAIL_DIR=$(THUMBNAIL_DIR) MAGICBOXIE_TRANSCODE_DIR=$(TRANSCODE_DIR) $(VENV)/bin/magicboxie-device

# Runs the test suite in the same venv the app runs in on the Pi.
pi-test:
	$(VENV)/bin/pip install -e ".[dev]"
	$(VENV)/bin/python -m pytest -v tests

# Renders deploy/magicboxie-device.service.in (user/paths filled in) to
# /etc/systemd/system and enables it to start on boot. Doesn't start it -
# run `make pi-start` (or reboot) after.
pi-service: pi-setup
	sed \
		-e 's|@USER@|'"$$(whoami)"'|g' \
		-e 's|@REPO_DIR@|$(CURDIR)|g' \
		-e 's|@MOVIES_DIR@|$(CONTENT_DIR)|g' \
		-e 's|@THUMBNAIL_DIR@|$(THUMBNAIL_DIR)|g' \
		-e 's|@TRANSCODE_DIR@|$(TRANSCODE_DIR)|g' \
		-e 's|@HOME_SERVER_URL@|$(HOME_SERVER_URL)|g' \
		-e 's|@HOME_SERVER_PASSWORD@|$(HOME_SERVER_PASSWORD)|g' \
		deploy/magicboxie-device.service.in | sudo tee $(SERVICE_FILE) >/dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable $(SERVICE_NAME)
	@echo "Service installed and enabled - run 'make pi-start' to start it now."

# Install the open AP profile without interrupting the current Wi-Fi session.
# Startup tries saved networks before activating the AP as a fallback.
pi-wifi-service:
	sudo install -d -m 700 /etc/NetworkManager/system-connections
	sudo install -m 600 deploy/magicboxie-hotspot.nmconnection /etc/NetworkManager/system-connections/magicboxie-hotspot.nmconnection
	sudo install -m 644 deploy/magicboxie-hotspot-dnsmasq.conf /etc/magicboxie-hotspot-dnsmasq.conf
	sudo dnsmasq --test --conf-file=/etc/magicboxie-hotspot-dnsmasq.conf
	sudo install -m 644 deploy/magicboxie-hotspot.service /etc/systemd/system/magicboxie-hotspot.service
	sudo systemctl enable --now NetworkManager
	sudo nmcli connection reload
	sudo install -d -m 755 /usr/local/lib/magicboxie
	sudo install -m 644 player_app/wifi_startup.py player_app/wifi_networks.py player_app/storage.py /usr/local/lib/magicboxie/
	sudo install -m 644 deploy/magicboxie-wifi-startup.service /etc/systemd/system/magicboxie-wifi-startup.service
	sudo systemctl disable magicboxie-hotspot
	sudo systemctl daemon-reload
	sudo systemctl enable magicboxie-wifi-startup

pi-wifi-start:
	@echo 'Trying saved Wi-Fi; MagicBoxie Device hotspot starts if none connects within 30 seconds.'
	sudo systemctl restart --no-block magicboxie-wifi-startup

pi-start:
	sudo systemctl start $(SERVICE_NAME)

pi-stop:
	sudo systemctl stop $(SERVICE_NAME)

pi-restart:
	sudo systemctl restart $(SERVICE_NAME)

# After pulling new code: reinstall into the venv and restart the service.
pi-redeploy: pi-sparse-refresh pi-service pi-wifi-service
	sudo systemctl restart $(SERVICE_NAME)
	$(MAKE) pi-wifi-start

# Blocks until nothing's selected to play (PlaybackStatus.STOPPED, reported
# as "stopped" by /api/status - see playback_controller.py's refresh_status
# and web_service.py's _get_status), so pi-self-update's restart never cuts
# off a movie mid-playback. Polls the local HTTP API rather than the BLE
# characteristic since that's always running regardless of
# MAGICBOXIE_TRANSPORT (see main.py's _run()) and trivial to curl. No
# timeout: if something's playing back-to-back for hours, the update just
# waits for the next natural gap - the alternative (forcing it) is exactly
# the interruption this exists to avoid. A dead/unreachable HTTP API (server
# still starting up, one bad poll) reads as "" here, which never matches
# "stopped", so it just keeps polling rather than mistaking that for idle.
pi-wait-until-idle:
	@status=""; \
	while [ "$$status" != "stopped" ]; do \
		sleep 30; \
		status="$$(curl -s --max-time 5 http://localhost:$(HTTP_PORT)/api/status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))' 2>/dev/null)"; \
	done

# Pulls the latest code and redeploys, but only if the pull actually brought
# in new commits - startup after saved Wi-Fi connects and the daily
# magicboxie-self-update.timer both run this, so a check with nothing new to
# install never interrupts whatever's playing with a pointless restart.
# Waits for playback to be idle (pi-wait-until-idle above) before
# redeploying, so even a real update never cuts off a movie already in
# progress. --ff-only rather than pi-pull's plain `git pull`: this runs
# unattended, so a diverged history should fail cleanly instead of
# attempting a merge with no one around to resolve it. Skips the pull
# entirely if the working tree has local changes (shouldn't happen on a
# real device, but could during development on one) rather than risking
# git's merge machinery touching them.
pi-self-update:
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "pi-self-update: local changes present in $(CURDIR) - skipping"; \
		exit 0; \
	fi; \
	before="$$(git rev-parse HEAD)"; \
	python3 -m player_app.update_status checking $$$$; \
	$(MAKE) -s pi-sparse-refresh; \
	if ! git pull --ff-only; then \
		echo "pi-self-update: git pull failed (no internet?) - will retry on the next scheduled run"; \
		python3 -m player_app.update_status clear $$$$; \
		exit 0; \
	fi; \
	after="$$(git rev-parse HEAD)"; \
	installed="$$(cat .git/magicboxie-installed-revision 2>/dev/null)"; \
	if [ "$$installed" = "$$after" ]; then \
		echo "pi-self-update: already up to date"; \
		python3 -m player_app.update_status current $$$$; \
	else \
		echo "pi-self-update: $$before -> $$after, waiting for playback to be idle before redeploying"; \
		python3 -m player_app.update_status waiting $$$$; \
		trap 'python3 -m player_app.update_status clear $$$$' EXIT HUP INT TERM; \
		$(MAKE) pi-wait-until-idle || exit $$?; \
		echo "pi-self-update: idle now, redeploying"; \
		python3 -m player_app.update_status installing $$$$; \
		sleep 2; \
		$(MAKE) pi-redeploy || exit $$?; \
		python3 -c 'import sys; from pathlib import Path; from player_app.storage import atomic_write; atomic_write(Path(".git/magicboxie-installed-revision"), sys.argv[1].encode())' "$$after"; \
	fi

# Renders and enables magicboxie-self-update's service+timer (daily git
# pull, see pi-self-update above) - part of pi-install/pi so auto-update is
# on by default rather than a separate opt-in step. Only the .service has
# placeholders to fill in; the .timer is copied as-is.
pi-self-update-service:
	sed \
		-e 's|@USER@|'"$$(whoami)"'|g' \
		-e 's|@REPO_DIR@|$(CURDIR)|g' \
		deploy/magicboxie-self-update.service.in | sudo tee $(SELF_UPDATE_SERVICE_FILE) >/dev/null
	sudo cp deploy/magicboxie-self-update.timer.in $(SELF_UPDATE_TIMER_FILE)
	sed -e 's|@REPO_DIR@|$(CURDIR)|g' \
		deploy/magicboxie-boot-update.service.in | sudo tee $(BOOT_UPDATE_SERVICE_FILE) >/dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable --now $(SELF_UPDATE_NAME).timer
	sudo systemctl enable $(BOOT_UPDATE_NAME).service
	@echo "Self-update timer installed and enabled - runs daily, check with: systemctl list-timers $(SELF_UPDATE_NAME).timer"

# Boot-time update check: wait up to 30 seconds for internet (any interface),
# then queue the self-update service. No internet is not an error - the
# device just continues with the code it has; the daily timer retries later.
pi-boot-update:
	@python3 -m player_app.update_status internet $$$$; \
	for i in $$(seq 1 30); do \
		if git -c safe.directory="$(CURDIR)" ls-remote --exit-code origin HEAD >/dev/null 2>&1; then \
			echo "pi-boot-update: internet is up, requesting self-update"; \
			python3 -m player_app.update_status clear-boot $$$$; \
			systemctl --no-block start $(SELF_UPDATE_NAME).service; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "pi-boot-update: no internet after 30 seconds - continuing without updating"; \
	python3 -m player_app.update_status no_internet $$$$

pi-logs:
	journalctl -u $(SERVICE_NAME) -f

pi-uninstall:
	sudo systemctl disable --now magicboxie-wifi-startup 2>/dev/null || true
	sudo rm -f /etc/systemd/system/magicboxie-wifi-startup.service /usr/local/lib/magicboxie/wifi_startup.py
	sudo systemctl disable --now magicboxie-hotspot 2>/dev/null || true
	sudo nmcli connection delete magicboxie-hotspot 2>/dev/null || true
	sudo rm -f /etc/systemd/system/magicboxie-hotspot.service /etc/magicboxie-hotspot-dnsmasq.conf
	sudo systemctl disable --now $(SERVICE_NAME) 2>/dev/null || true
	sudo systemctl disable --now $(SELF_UPDATE_NAME).timer 2>/dev/null || true
	sudo systemctl disable $(BOOT_UPDATE_NAME).service 2>/dev/null || true
	sudo rm -f $(SERVICE_FILE) $(SELF_UPDATE_SERVICE_FILE) $(SELF_UPDATE_TIMER_FILE) $(BOOT_UPDATE_SERVICE_FILE)
	sudo systemctl daemon-reload

pi-clean:
	rm -rf $(VENV)
