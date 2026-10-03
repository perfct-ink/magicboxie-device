FROM python:3.11-slim-bookworm AS base

# mpv for playback, ffmpeg (ffprobe) for reading movie durations when scanning
# the library. build-essential/python3-dev/libjpeg-dev/zlib1g-dev let evdev
# (keyboard input) and Pillow (idle-screen thumbnail grid) build from source -
# needed on the target Pi Zero W's 32-bit ARMv6, which PyPI rarely ships
# prebuilt wheels for.
RUN apt-get update && apt-get install -y --no-install-recommends \
    mpv \
    ffmpeg \
    build-essential \
    python3-dev \
    libjpeg-dev \
    zlib1g-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pi/pyproject.toml ./
COPY pi/player_app ./player_app
COPY pi/web ./web
RUN pip install --no-cache-dir .

ENV MAGICBOXIE_MOVIES_DIR=/movies
EXPOSE 8000

ENTRYPOINT ["magicboxie-player"]

# `make test` / `docker build --target test`: runs the suite inside the same
# environment the app ships in, so `make` never depends on a local Python.
FROM base AS test
RUN pip install --no-cache-dir .[dev]
COPY tests ./tests
ENTRYPOINT []
CMD ["python", "-m", "pytest", "-v"]
