import asyncio
from unittest.mock import AsyncMock, patch

from player_app.services.mdns_service import SERVICE_TYPE, MdnsAdvertiser

# Real AsyncZeroconf opens real multicast sockets, which is both unnecessary
# for what this module needs to guarantee and unreliable in CI (the
# Makefile's containerized `test` target doesn't run with host networking).
# Patching it out keeps this a pure unit test of MdnsAdvertiser's wiring.
_PATCH_TARGET = "player_app.services.mdns_service.AsyncZeroconf"


def test_start_registers_service_with_expected_info():
    async def scenario():
        with patch(_PATCH_TARGET) as mock_zeroconf_cls:
            mock_zc = mock_zeroconf_cls.return_value
            mock_zc.async_register_service = AsyncMock()

            advertiser = MdnsAdvertiser("MagicBoxie", "192.168.1.50", 8000)
            await advertiser.start()

            mock_zc.async_register_service.assert_awaited_once()
            info = mock_zc.async_register_service.await_args.args[0]
            assert info.type == SERVICE_TYPE
            assert info.name == f"MagicBoxie.{SERVICE_TYPE}"
            assert info.port == 8000
            assert info.server == "MagicBoxie.local."

    asyncio.run(scenario())


def test_stop_unregisters_and_closes():
    async def scenario():
        with patch(_PATCH_TARGET) as mock_zeroconf_cls:
            mock_zc = mock_zeroconf_cls.return_value
            mock_zc.async_unregister_service = AsyncMock()
            mock_zc.async_close = AsyncMock()

            advertiser = MdnsAdvertiser("MagicBoxie", "192.168.1.50", 8000)
            await advertiser.stop()

            mock_zc.async_unregister_service.assert_awaited_once()
            mock_zc.async_close.assert_awaited_once()

    asyncio.run(scenario())
