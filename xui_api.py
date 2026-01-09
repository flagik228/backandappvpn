import uuid
import asyncio
import requests
import urllib3
from datetime import datetime, timedelta

# ============================
# 🔥 FIX SSL FOR py3xui
# ============================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_old_request = requests.Session.request

def _patched_request(self, method, url, **kwargs):
    kwargs["verify"] = False
    return _old_request(self, method, url, **kwargs)

requests.Session.request = _patched_request
# ============================

from py3xui import Api
from py3xui.client.client import Client  # корректный импорт клиента



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

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
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
            expiry_time=expiry_time
        )

        await asyncio.to_thread(self.api.client.add, inbound_id, [new_client])

        return {
            "uuid": client_uuid,
            "email": email,
            "expiry_time": expiry_time
        }


    async def extend_client(self, inbound_id: int, email: str, days: int):
        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )
        if not inbound:
            raise Exception("Inbound not found")

        clients = inbound.settings.clients or []

        client_index = None
        target_client = None

        for idx, c in enumerate(clients):
            if c.email == email:
                client_index = idx
                target_client = c
                break

        if target_client is None:
            raise Exception("Client not found in inbound")

        now_ms = int(datetime.utcnow().timestamp() * 1000)
        add_ms = days * 86400000

        current_expiry = target_client.expiry_time or 0

        if current_expiry > now_ms:
            target_client.expiry_time = current_expiry + add_ms
        else:
            target_client.expiry_time = now_ms + add_ms

        target_client.enable = True

        # 🔥 ВАЖНО: update по INDEX, а не UUID
        await asyncio.to_thread(
            self.api.client.update,
            inbound_id,
            client_index,
            target_client
        )

        return {
            "email": target_client.email,
            "old_expiry": current_expiry,
            "new_expiry": target_client.expiry_time
        }


    async def remove_client(self, inbound_id: int, email: str):
        await self.login()

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
        if not inbound:
            raise Exception("Inbound не найден")

        for idx, client in enumerate(inbound.settings.clients or []):
            if client.email == email:
                await asyncio.to_thread(
                    self.api.client.delete,
                    inbound_id,
                    idx
                )
                return True

        raise Exception("Клиент не найден")