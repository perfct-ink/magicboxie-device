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
SERVICE_NAME := magicboxie-device
SERVICE_FILE := /etc/systemd/system/$(SERVICE_NAME).service

.PHONY: all setup dev build test clean seed-movies \
	pi pi-pull pi-install pi-setup pi-seed-movies pi-run pi-test pi-service pi-start pi-stop \
	pi-restart pi-redeploy pi-logs pi-uninstall pi-clean

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
# the systemd service, and starts it. (The service itself gets video/input/
# bluetooth access straight from its unit file's SupplementaryGroups, so it
# doesn't need the installing shell's own group membership to have
# refreshed - that only matters if you separately use `make pi-run`.)
pi-install: pi-setup pi-seed-movies pi-service pi-start
	@echo "pi-install complete - MagicBoxie is running and will start automatically on boot."
	@echo "Check status with: make pi-logs"

# Day-to-day version of pi-install, for after the device is already set up:
# pulls whatever's new, re-runs setup (covers newly-added system deps or a
# changed pyproject.toml - a no-op otherwise), re-renders the systemd unit
# (covers changes to deploy/magicboxie-device.service.in), and restarts.
# Every step is idempotent, so this is safe to re-run any time you've
# pushed changes and want the Pi caught up and running them.
pi: pi-pull pi-setup pi-service
	sudo systemctl restart $(SERVICE_NAME)
	@echo "Pi is set up, deployed, and running - check status with: make pi-logs"

pi-pull:
	git pull

# System packages (mpv/ffmpeg/bluez + build headers for evdev/Pillow) and a
# venv with the app installed. Adds the invoking user to the video/input/
# bluetooth groups it needs for DRM output, keyboard Escape-to-stop, and
# BLE - re-login (or reboot) is required for that group change to apply.
pi-setup:
	@command -v apt-get >/dev/null || { echo "apt-get not found - pi-* targets are for Raspberry Pi OS/Debian"; exit 1; }
	sudo apt-get update
	sudo apt-get install -y --no-install-recommends \
		python3-venv python3-dev build-essential \
		mpv ffmpeg libjpeg-dev zlib1g-dev \
		fonts-dejavu-core \
		bluez dbus
	sudo usermod -aG video,input,bluetooth "$$(whoami)"
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e .
	sudo mkdir -p $(THUMBNAIL_DIR) $(TRANSCODE_DIR) $(CONTENT_DIR)
	sudo chown "$$(whoami)" $(THUMBNAIL_DIR) $(TRANSCODE_DIR) $(CONTENT_DIR)
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

pi-start:
	sudo systemctl start $(SERVICE_NAME)

pi-stop:
	sudo systemctl stop $(SERVICE_NAME)

pi-restart:
	sudo systemctl restart $(SERVICE_NAME)

# After pulling new code: reinstall into the venv and restart the service.
pi-redeploy:
	$(VENV)/bin/pip install -e .
	sudo systemctl restart $(SERVICE_NAME)

pi-logs:
	journalctl -u $(SERVICE_NAME) -f

pi-uninstall:
	sudo systemctl disable --now $(SERVICE_NAME) 2>/dev/null || true
	sudo rm -f $(SERVICE_FILE)
	sudo systemctl daemon-reload

pi-clean:
	rm -rf $(VENV)
