# Device (Raspberry Pi) targets - run from pi/, or from the repo root where
# the top-level Makefile forwards * here.
VENV := .venv
THUMBNAIL_DIR := /var/lib/magicboxie/thumbnails
TRANSCODE_DIR := /var/lib/magicboxie/transcoded
# MagicBoxie-web's LAN hostname - see player_app/services/home_sync_service.py.
# Override (e.g. to empty) for a device with no home server to sync with.
HOME_SERVER_URL := http://magicboxie.lan
# The home server login lives in a root-readable file outside Git, loaded by
# the unit (EnvironmentFile), so redeploys and self-updates never lose it.
# Never commit a real password: edit this file on the device, or pass it once
# at deploy time with `make service HOME_SERVER_PASSWORD=...`.
HOME_SERVER_PASSWORD :=
HOME_SERVER_ENV := /var/lib/magicboxie/home-server.env
# Fixed absolute path on the Pi where content lives - independent of wherever
# this repo happens to be checked out, unlike MOVIES_DIR above (which is
# Docker-dev-only, relative to the repo, and unrelated to the real device).
CONTENT_DIR := /content
# Matches MAGICBOXIE_HTTP_PORT's own default (see main.py) - only used here
# to poll the device's own /api/status from wait-until-idle below, so
# override this too if you've overridden that.
HTTP_PORT := 8000
SERVICE_NAME := magicboxie-player
SERVICE_FILE := /etc/systemd/system/$(SERVICE_NAME).service
SELF_UPDATE_NAME := magicboxie-self-update
SELF_UPDATE_SERVICE_FILE := /etc/systemd/system/$(SELF_UPDATE_NAME).service
SELF_UPDATE_TIMER_FILE := /etc/systemd/system/$(SELF_UPDATE_NAME).timer
BOOT_UPDATE_NAME := magicboxie-boot-update
BOOT_UPDATE_SERVICE_FILE := /etc/systemd/system/$(BOOT_UPDATE_NAME).service

.PHONY: deploy pull install setup seed-movies run test service start stop \
	restart redeploy wait-until-idle self-update self-update-service logs \
	uninstall clean wifi-service wifi-start sparse-refresh boot-update repair-checkout

# --- Raspberry Pi (native, no Docker) -------------------------------------
# The real device: installs system packages + a venv directly on the Pi and
# runs the daemon as a systemd service (BLE transport, DRM/KMS HDMI output).
# Run these on the Pi itself, over SSH or a directly attached keyboard.

# One-shot: after copying this directory onto the Pi, `make install` is
# the single command that gets a running, boot-persistent device - installs
# packages, seeds sample movies into /content if it's empty, installs+enables
# the systemd service (plus its daily self-update timer), and starts it.
# (The service itself gets video/input/bluetooth access straight from its
# unit file's SupplementaryGroups, so it doesn't need the installing shell's
# own group membership to have refreshed - that only matters if you
# separately use `make run`.)
install: setup seed-movies service self-update-service wifi-service start wifi-start
	@echo "install complete - MagicBoxie is running and will start automatically on boot."
	@echo "Check status with: make logs"

# Day-to-day version of install, for after the device is already set up:
# pulls whatever's new, re-runs setup (covers newly-added system deps or a
# changed pyproject.toml - a no-op otherwise), re-renders the systemd units
# (covers changes to either system/*.in template), and restarts. Every step
# is idempotent, so this is safe to re-run any time you've pushed changes
# and want the Pi caught up and running them.
deploy: pull setup service self-update-service wifi-service
	sudo systemctl restart $(SERVICE_NAME)
	$(MAKE) wifi-start
	@echo "Pi is set up, deployed, and running - check status with: make logs"

# Devices installed from install.sh use a sparse checkout; refresh its patterns
# so deploy files added since the original install are checked out.
sparse-refresh:
	@if [ "$$(git config --get core.sparseCheckout)" = "true" ]; then \
		git -C "$$(git rev-parse --show-toplevel)" sparse-checkout set --no-cone /pi/ /Makefile; \
	fi

pull: sparse-refresh
	git pull

# System packages (mpv/ffmpeg/bluez + build headers for evdev/Pillow, curl
# for wait-until-idle's polling below) and a venv with the app installed.
# Adds the invoking user to the video/input/bluetooth groups it needs for
# DRM output, keyboard Escape-to-stop, and BLE - re-login (or reboot) is
# required for that group change to apply.
setup: sparse-refresh
	@command -v apt-get >/dev/null || { echo "apt-get not found - these targets are for Raspberry Pi OS/Debian"; exit 1; }
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
	@# Logs stay in RAM: a device that is unplugged often should not write the journal to the SD card.
	sudo rm -rf /etc/systemd/journald.conf.d/magicboxie.conf /var/log/journal
	sudo systemctl restart systemd-journald
	printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff\n' "$$(whoami)" | sudo tee /etc/sudoers.d/magicboxie >/dev/null
	sudo chmod 440 /etc/sudoers.d/magicboxie
	sudo visudo -cf /etc/sudoers.d/magicboxie >/dev/null || { sudo rm -f /etc/sudoers.d/magicboxie; exit 1; }
	sudo usermod -aG video,input,bluetooth "$$(whoami)"
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e .
	sudo mkdir -p $(THUMBNAIL_DIR) $(TRANSCODE_DIR) $(CONTENT_DIR)
	sudo chown "$$(whoami)" $(THUMBNAIL_DIR) $(TRANSCODE_DIR) $(CONTENT_DIR)
	sudo install -d -m 700 -o "$$(whoami)" /var/lib/magicboxie
	@if [ ! -e /var/lib/magicboxie/wifi-networks.json ]; then \
		sudo install -m 600 -o "$$(whoami)" system/wifi-networks.json /var/lib/magicboxie/wifi-networks.json; \
	fi
	sudo chown "$$(whoami)" /var/lib/magicboxie/wifi-networks.json
	sudo chmod 600 /var/lib/magicboxie/wifi-networks.json
	@echo "setup complete - log out/in (or reboot) so the new group membership takes effect."

# Same sample-video seeding as `seed-movies`, but into the real device's
# fixed content directory, using the ffmpeg installed straight onto the Pi
# by setup instead of a Docker image.
seed-movies:
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
run: seed-movies
	MAGICBOXIE_MOVIES_DIR=$(CONTENT_DIR) MAGICBOXIE_THUMBNAIL_DIR=$(THUMBNAIL_DIR) MAGICBOXIE_TRANSCODE_DIR=$(TRANSCODE_DIR) $(VENV)/bin/magicboxie-player

# Runs the test suite in the same venv the app runs in on the Pi.
test:
	$(VENV)/bin/pip install -e ".[dev]"
	$(VENV)/bin/python -m pytest -v ../tests

# Renders system/magicboxie-player.service.in (user/paths filled in) to
# /etc/systemd/system and enables it to start on boot. Doesn't start it -
# run `make start` (or reboot) after.
service: setup
	@# Renamed from magicboxie-device: retire the old unit so two daemons never contend for the ports.
	-sudo systemctl disable --now magicboxie-device 2>/dev/null
	sudo rm -f /etc/systemd/system/magicboxie-device.service
	@# Home server password: an explicit value wins; otherwise keep one an earlier
	@# deploy baked into the unit (moved to the env file once); otherwise seed a template.
	@if [ -n "$(HOME_SERVER_PASSWORD)" ]; then \
		printf 'MAGICBOXIE_HOME_SERVER_PASSWORD="%s"\n' '$(HOME_SERVER_PASSWORD)' | sudo tee $(HOME_SERVER_ENV) >/dev/null && sudo chmod 600 $(HOME_SERVER_ENV); \
	elif [ ! -e $(HOME_SERVER_ENV) ]; then \
		old="$$(sed -n 's/^Environment=MAGICBOXIE_HOME_SERVER_PASSWORD=//p' $(SERVICE_FILE) 2>/dev/null)"; \
		printf '# Home server login (see pi/Makefile). One line, then: sudo systemctl restart magicboxie-player\nMAGICBOXIE_HOME_SERVER_PASSWORD="%s"\n' "$$old" | sudo tee $(HOME_SERVER_ENV) >/dev/null && sudo chmod 600 $(HOME_SERVER_ENV); \
	fi
	sed \
		-e 's|@USER@|'"$$(whoami)"'|g' \
		-e 's|@REPO_DIR@|$(CURDIR)|g' \
		-e 's|@MOVIES_DIR@|$(CONTENT_DIR)|g' \
		-e 's|@HOME_SERVER_ENV@|$(HOME_SERVER_ENV)|g' \
		-e 's|@THUMBNAIL_DIR@|$(THUMBNAIL_DIR)|g' \
		-e 's|@TRANSCODE_DIR@|$(TRANSCODE_DIR)|g' \
		-e 's|@HOME_SERVER_URL@|$(HOME_SERVER_URL)|g' \
		system/magicboxie-player.service.in | sudo tee $(SERVICE_FILE) >/dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable $(SERVICE_NAME)
	@echo "Service installed and enabled - run 'make start' to start it now."

# Install the open AP profile without interrupting the current Wi-Fi session.
# Startup tries saved networks before activating the AP as a fallback.
wifi-service:
	sudo install -d -m 700 /etc/NetworkManager/system-connections
	sudo install -m 600 system/magicboxie-hotspot.nmconnection /etc/NetworkManager/system-connections/magicboxie-hotspot.nmconnection
	sudo install -m 644 system/magicboxie-hotspot-dnsmasq.conf /etc/magicboxie-hotspot-dnsmasq.conf
	sudo dnsmasq --test --conf-file=/etc/magicboxie-hotspot-dnsmasq.conf
	sudo install -m 644 system/magicboxie-hotspot.service /etc/systemd/system/magicboxie-hotspot.service
	sudo systemctl enable --now NetworkManager
	sudo nmcli connection reload
	sudo install -d -m 755 /usr/local/lib/magicboxie
	sudo install -m 644 player_app/wifi_startup.py player_app/wifi_networks.py player_app/storage.py /usr/local/lib/magicboxie/
	sudo install -m 644 system/magicboxie-wifi-startup.service /etc/systemd/system/magicboxie-wifi-startup.service
	sudo systemctl disable magicboxie-hotspot
	sudo systemctl daemon-reload
	sudo systemctl enable magicboxie-wifi-startup

wifi-start:
	@echo 'Trying saved Wi-Fi; MagicBoxie Player hotspot starts if none connects within 30 seconds.'
	sudo systemctl restart --no-block magicboxie-wifi-startup

start:
	sudo systemctl start $(SERVICE_NAME)

stop:
	sudo systemctl stop $(SERVICE_NAME)

restart:
	sudo systemctl restart $(SERVICE_NAME)

# After pulling new code: reinstall into the venv and restart the service.
redeploy: sparse-refresh service wifi-service
	sudo systemctl restart $(SERVICE_NAME)
	$(MAKE) wifi-start

# Blocks until nothing's selected to play (PlaybackStatus.STOPPED, reported
# as "stopped" by /api/status - see playback_controller.py's refresh_status
# and web_service.py's _get_status), so self-update's restart never cuts
# off a movie mid-playback. Polls the local HTTP API rather than the BLE
# characteristic since that's always running regardless of
# MAGICBOXIE_TRANSPORT (see main.py's _run()) and trivial to curl. No
# timeout: if something's playing back-to-back for hours, the update just
# waits for the next natural gap - the alternative (forcing it) is exactly
# the interruption this exists to avoid. A dead/unreachable HTTP API (server
# still starting up, one bad poll) reads as "" here, which never matches
# "stopped", so it just keeps polling rather than mistaking that for idle.
wait-until-idle:
	@status=""; \
	while [ "$$status" != "stopped" ]; do \
		sleep 30; \
		status="$$(curl -s --max-time 5 http://localhost:$(HTTP_PORT)/api/status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))' 2>/dev/null)"; \
	done

# Power loss mid-pull or mid-checkout can leave a stale index.lock, empty git
# objects, or half-written tracked files. A device checkout (sparse, from
# install.sh) is deployment-only - changes arrive through git - so restore its
# tracked files to HEAD. Runs before the daemon starts (ExecStartPre) and
# before every self-update, so a damaged checkout never keeps the device from
# booting or updating. Never touches untracked files (the venv, caches), and
# does nothing in a normal development checkout (no sparse-checkout).
repair-checkout:
	@[ "$$(git config --get core.sparseCheckout)" = "true" ] || exit 0; \
	gitdir="$$(git rev-parse --git-dir 2>/dev/null)" || exit 0; \
	pgrep -x git >/dev/null && exit 0; \
	rm -f "$$gitdir/index.lock"; \
	if ! git rev-parse --verify -q 'HEAD^{commit}' >/dev/null || ! git status --porcelain --untracked-files=no >/dev/null 2>&1; then \
		echo "repair-checkout: git data looks damaged - removing empty objects"; \
		find "$$gitdir/objects" -type f -size 0 -delete; \
	fi; \
	if [ -n "$$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then \
		echo "repair-checkout: restoring tracked files to HEAD:"; \
		git status --short --untracked-files=no; \
		git reset --hard HEAD || true; \
	fi

# Pulls the latest code and redeploys, but only if the pull actually brought
# in new commits - startup after saved Wi-Fi connects and the daily
# magicboxie-self-update.timer both run this, so a check with nothing new to
# install never interrupts whatever's playing with a pointless restart.
# Waits for playback to be idle (wait-until-idle above) before
# redeploying, so even a real update never cuts off a movie already in
# progress. --ff-only rather than pull's plain `git pull`: this runs
# unattended, so a diverged history should fail cleanly instead of
# attempting a merge with no one around to resolve it. Skips the pull
# entirely if the working tree has local changes (shouldn't happen on a
# real device, but could during development on one) rather than risking
# git's merge machinery touching them.
self-update:
	@$(MAKE) -s repair-checkout; \
	if ! git rev-parse --verify -q 'HEAD^{commit}' >/dev/null; then \
		echo "self-update: HEAD is unreadable - fetching a fresh copy"; \
		git fetch origin HEAD && git reset --hard FETCH_HEAD || { echo "self-update: repair failed - will retry later"; exit 0; }; \
	fi; \
	before="$$(git rev-parse HEAD)"; \
	python3 -m player_app.update_status checking $$$$; \
	$(MAKE) -s sparse-refresh; \
	if ! git pull --ff-only; then \
		echo "self-update: git pull failed (no internet?) - will retry on the next scheduled run"; \
		python3 -m player_app.update_status clear $$$$; \
		exit 0; \
	fi; \
	after="$$(git rev-parse HEAD)"; \
	installed="$$(cat $$(git rev-parse --git-dir)/magicboxie-installed-revision 2>/dev/null)"; \
	if [ "$$installed" = "$$after" ]; then \
		echo "self-update: already up to date"; \
		python3 -m player_app.update_status current $$$$; \
	else \
		echo "self-update: $$before -> $$after, waiting for playback to be idle before redeploying"; \
		python3 -m player_app.update_status waiting $$$$; \
		trap 'python3 -m player_app.update_status clear $$$$' EXIT HUP INT TERM; \
		$(MAKE) wait-until-idle || exit $$?; \
		echo "self-update: idle now, redeploying"; \
		python3 -m player_app.update_status installing $$$$; \
		sleep 2; \
		$(MAKE) redeploy || exit $$?; \
		python3 -c 'import sys; from pathlib import Path; from player_app.storage import atomic_write; atomic_write(Path(sys.argv[2]) / "magicboxie-installed-revision", sys.argv[1].encode())' "$$after" "$$(git rev-parse --git-dir)"; \
	fi

# Renders and enables magicboxie-self-update's service+timer (daily git
# pull, see self-update above) - part of install/pi so auto-update is
# on by default rather than a separate opt-in step. Only the .service has
# placeholders to fill in; the .timer is copied as-is.
self-update-service:
	sed \
		-e 's|@USER@|'"$$(whoami)"'|g' \
		-e 's|@REPO_DIR@|$(CURDIR)|g' \
		system/magicboxie-self-update.service.in | sudo tee $(SELF_UPDATE_SERVICE_FILE) >/dev/null
	sudo cp system/magicboxie-self-update.timer.in $(SELF_UPDATE_TIMER_FILE)
	sed -e 's|@REPO_DIR@|$(CURDIR)|g' \
		system/magicboxie-boot-update.service.in | sudo tee $(BOOT_UPDATE_SERVICE_FILE) >/dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable --now $(SELF_UPDATE_NAME).timer
	sudo systemctl enable $(BOOT_UPDATE_NAME).service
	@echo "Self-update timer installed and enabled - runs daily, check with: systemctl list-timers $(SELF_UPDATE_NAME).timer"

# Boot-time update check: wait up to 30 seconds for internet (any interface),
# then queue the self-update service. No internet is not an error - the
# device just continues with the code it has; the daily timer retries later.
boot-update:
	@python3 -m player_app.update_status internet $$$$; \
	for i in $$(seq 1 30); do \
		if git -c safe.directory="*" ls-remote --exit-code origin HEAD >/dev/null 2>&1; then \
			echo "boot-update: internet is up, requesting self-update"; \
			python3 -m player_app.update_status clear-boot $$$$; \
			systemctl --no-block start $(SELF_UPDATE_NAME).service; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "boot-update: no internet after 30 seconds - continuing without updating"; \
	python3 -m player_app.update_status no_internet $$$$

logs:
	journalctl -u $(SERVICE_NAME) -f

uninstall:
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

clean:
	rm -rf $(VENV)
