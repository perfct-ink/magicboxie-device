import asyncio
from unittest.mock import AsyncMock, patch

from player_app.util import ThrottleStatus, cpu_temperature_celsius, get_throttle_status

# Real vcgencmd subprocess creation is patched out so these tests don't
# depend on real Pi hardware - matching how test_wifi_provisioning.py
# patches out nmcli.
_PATCH_TARGET = "player_app.util.asyncio.create_subprocess_exec"


class FakeProcess:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b""


def test_cpu_temperature_celsius_returns_none_without_a_real_thermal_zone(tmp_path):
    """The Docker dev container this test suite runs in has no real
    thermal zone - confirms that's handled gracefully rather than raising."""
    assert cpu_temperature_celsius() is None


def test_get_throttle_status_parses_no_throttling():
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=FakeProcess(stdout=b"throttled=0x0\n"))):
        status = asyncio.run(get_throttle_status())

    assert status == ThrottleStatus(under_voltage=False, throttled=False)


def test_get_throttle_status_parses_under_voltage_now():
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=FakeProcess(stdout=b"throttled=0x50005\n"))):
        status = asyncio.run(get_throttle_status())

    assert status == ThrottleStatus(under_voltage=True, throttled=True)


def test_get_throttle_status_ignores_since_boot_only_bits():
    """0x10000 is "under-voltage has occurred since boot" - not happening
    right now, which is what this reports."""
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=FakeProcess(stdout=b"throttled=0x10000\n"))):
        status = asyncio.run(get_throttle_status())

    assert status == ThrottleStatus(under_voltage=False, throttled=False)


def test_get_throttle_status_returns_none_when_vcgencmd_is_missing():
    with patch(_PATCH_TARGET, new=AsyncMock(side_effect=OSError("not found"))):
        status = asyncio.run(get_throttle_status())

    assert status is None


def test_get_throttle_status_returns_none_on_nonzero_exit():
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=FakeProcess(returncode=1))):
        status = asyncio.run(get_throttle_status())

    assert status is None


def test_get_throttle_status_returns_none_on_unexpected_output():
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=FakeProcess(stdout=b"not the expected format\n"))):
        status = asyncio.run(get_throttle_status())

    assert status is None
