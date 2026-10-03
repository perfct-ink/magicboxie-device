"""mDNS/Bonjour advertisement of the device's HTTP API.

Independent of BLE: lets the iOS app discover the device's WiFi address
without an active BLE connection first. The BLE GATT service's
NETWORK_INFO_CHARACTERISTIC_UUID (see ble_service.py) advertises the same
address as the fallback path for when mDNS multicast doesn't reach the
device (e.g. client-isolated WiFi) - the two are independent sources for
the same information, not alternatives to choose between.
"""
from __future__ import annotations

import logging
import socket

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

logger = logging.getLogger(__name__)

# Must match WiFiDeviceDiscovery.serviceType in the iOS app exactly.
SERVICE_TYPE = "_magicboxie._tcp.local."


class MdnsAdvertiser:
    def __init__(self, device_name: str, local_ip: str, port: int):
        self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        self._info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"{device_name}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            server=f"{device_name}.local.",
            properties={"name": device_name},
        )

    async def start(self) -> None:
        await self._zc.async_register_service(self._info)
        logger.info("Advertising mDNS %s on %s:%d", self._info.name, self._info.server, self._info.port)

    async def stop(self) -> None:
        await self._zc.async_unregister_service(self._info)
        await self._zc.async_close()
