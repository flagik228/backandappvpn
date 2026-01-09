import uuid
import asyncio
import httpx
import ssl
import urllib3
import requests
from datetime import datetime, timedelta
from py3xui import Api
from py3xui.client.client import Client  # корректный импорт клиента



# ==========================================================
# 🔥 CRITICAL FIX: force-disable SSL verification in requests
# ==========================================================

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_original_session_init = requests.Session.__init__

def _patched_session_init(self, *args, **kwargs):
    _original_session_init(self, *args, **kwargs)
    self.verify = False

requests.Session.__init__ = _patched_session_init

class XUIApi:
    """API-обёртка над py3xui, совместимая с 3x-ui 2.x/3.x"""

    def __init__(self, api_url: str, username: str, password: str):
        self.api = Api(
            host=api_url,
            username=username,
            password=password
        )
        self._logged_in = False
        self._lock = asyncio.Lock()

    async def login(self):
        async with self._lock:
            if not self._logged_in:
                await asyncio.to_thread(self.api.login)
                self._logged_in = True

    # ---------------- INBOUNDS ----------------
    async def get_inbounds(self):
        await self.login()
        return await asyncio.to_thread(self.api.inbound.get_list)


    async def get_inbound_by_port(self, port: int):
        """получить inbound по порту"""
        inbounds = await self.get_inbounds()
        for inbound in inbounds:
            if inbound.port == port:
                return inbound
        return None


    async def get_inbound(self, inbound_id: int):
        await self.login()
        return await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
    

    # ————————— CLIENTS —————————
    async def add_client(self, inbound_id: int, email: str, days: int):
        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id, inbound_id
        )
        if not inbound:
            raise Exception("Inbound не найден")

        client_uuid = str(uuid.uuid4())
        expiry_time = int(
            (datetime.utcnow() + timedelta(days=days)).timestamp() * 1000
        )

        new_client = Client(
            id=client_uuid,
            email=email,
            enable=True,
            expiryTime=expiry_time
        )

        await asyncio.to_thread(
            self.api.client.add,
            inbound_id,
            [new_client]
        )

        return {
            "uuid": client_uuid,
            "email": email,
            "expiry_time": expiry_time
        }

    async def extend_client(self, inbound_id: int, email: str, days: int):
        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id, inbound_id
        )
        if not inbound:
            raise Exception("Inbound не найден")

        now_ms = int(datetime.utcnow().timestamp() * 1000)

        for client in inbound.settings.clients or []:
            if client.email == email:
                if client.expiryTime and client.expiryTime > now_ms:
                    client.expiryTime += days * 86400000
                else:
                    client.expiryTime = now_ms + days * 86400000

                client.enable = True

                await asyncio.to_thread(
                    self.api.client.update,
                    client.id,
                    client
                )
                return True

        raise Exception("Клиент не найден")

    async def remove_client(self, inbound_id: int, email: str):
        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id, inbound_id
        )
        if not inbound:
            raise Exception("Inbound не найден")

        for client in inbound.settings.clients or []:
            if client.email == email:
                await asyncio.to_thread(
                    self.api.client.delete,
                    inbound_id,
                    client.id
                )
                return True

        raise Exception("Клиент не найден")