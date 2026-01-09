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
            (datetime.utcnow() + timedelta(days=days)).timestamp() * 1000)

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


    async def extend_client(self, inbound_id: int, client_uuid: str, days: int):
        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )
        if not inbound:
            raise Exception("Inbound not found")

        target = None
        for client in inbound.settings.clients or []:
            if client.id == client_uuid:
                target = client
                break

        if not target:
            raise Exception("Client not found by UUID")

        now_ms = int(datetime.utcnow().timestamp() * 1000)
        add_ms = days * 86400000

        current = target.expiry_time or 0
        target.expiry_time = current + add_ms if current > now_ms else now_ms + add_ms
        target.enable = True

        await asyncio.to_thread(
            self.api.client.update,
            inbound_id,
            target
        )

        return {
            "uuid": client_uuid,
            "old_expiry": current,
            "new_expiry": target.expiry_time
        }


    async def remove_client(self, inbound_id: int, client_uuid: str):
        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )

        for client in inbound.settings.clients or []:
            if client.id == client_uuid:
                await asyncio.to_thread(
                    self.api.client.delete,
                    inbound_id,
                    client.id
                )
                return True

        raise Exception("Client not found")