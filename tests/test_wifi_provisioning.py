import asyncio
from unittest.mock import AsyncMock, patch

from player_app.views.wifi_provisioning import apply_wifi_credentials

# Real nmcli subprocess creation is patched out so these tests don't depend
# on real network hardware - matching how test_transcode_service.py patches
# out ffmpeg rather than requiring a real encoder.
_PATCH_TARGET = "player_app.views.wifi_provisioning.asyncio.create_subprocess_exec"


class FakeProcess:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr


def test_apply_wifi_credentials_calls_nmcli_with_the_given_network(tmp_path):
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=FakeProcess())) as mock_exec:
        result = asyncio.run(apply_wifi_credentials("MyHotspot", "hunter2"))

    assert result is True
    mock_exec.assert_awaited_once_with(
        "nmcli", "dev", "wifi", "connect", "MyHotspot", "password", "hunter2",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def test_apply_wifi_credentials_returns_false_on_failure():
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=FakeProcess(returncode=1, stderr=b"wrong password"))):
        result = asyncio.run(apply_wifi_credentials("MyHotspot", "wrong"))

    assert result is False
