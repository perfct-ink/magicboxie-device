#!/bin/sh
# Bootstraps a MagicBoxie player on a fresh Raspberry Pi. Since the Pi has no
# other easy way to get files onto it and isn't online most of the time,
# this is meant to be curled and run once, while it does have internet
# (e.g. over SSH on the home WiFi during initial setup):
#
#   curl -fsSL https://raw.githubusercontent.com/kriogenx0/magicboxie-device/main/install.sh | sh
#
# It creates/updates a shallow sparse checkout containing only the player app
# and Pi installation files, then hands off to `make pi-install`. Docker files,
# tests, deployment tooling, and other development-only files never land on
# the device.
set -eu

REPO_URL="https://github.com/kriogenx0/magicboxie-device.git"
REPO_REF="${MAGICBOXIE_REF:-main}"
INSTALL_DIR="${MAGICBOXIE_INSTALL_DIR:-$HOME/magicboxie-player}"

configure_sparse_checkout() {
    git -C "$INSTALL_DIR" sparse-checkout init --no-cone
    git -C "$INSTALL_DIR" sparse-checkout set --no-cone \
        /pi/ \
        /Makefile
}

if ! command -v git >/dev/null 2>&1; then
    echo "git not found - installing..."
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends git
fi

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Found existing checkout at $INSTALL_DIR - updating..."
    configure_sparse_checkout
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$REPO_REF"
    git -C "$INSTALL_DIR" checkout "$REPO_REF"
    git -C "$INSTALL_DIR" reset --hard "origin/$REPO_REF"
else
    echo "Creating minimal checkout from $REPO_URL (ref $REPO_REF) in $INSTALL_DIR..."
    git clone --depth 1 --filter=blob:none --no-checkout --branch "$REPO_REF" \
        "$REPO_URL" "$INSTALL_DIR"
    configure_sparse_checkout
    git -C "$INSTALL_DIR" checkout "$REPO_REF"
fi

cd "$INSTALL_DIR"
make pi-install
