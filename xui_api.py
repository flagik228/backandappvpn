import httpx
from datetime import datetime, timedelta
from py3xui import Api
import uuid
import asyncio


class XUIApi:
    """Production-ready API-обёртка над py3xui Совместима с 3x-ui 2.x / 3.x """

    def __init__(self, api_url: str, username: str, password: str):
        self.api = Api(
            host=api_url,
            username=username,
            password=password,
        )
        # у тебя самоподписанный сертификат
        self.api.client.verify = False
        self._logged_in = False

    # ================= AUTH =================

    async def login(self):
        if self._logged_in:
            return

        await asyncio.to_thread(self.api.login)
        self._logged_in = True

    # ================= INBOUNDS =================

    async def get_inbounds(self):
        await self.login()
        return await asyncio.to_thread(self.api.inbound.get_list)

    async def get_inbound_by_port(self, port: int):
        inbounds = await self.get_inbounds()
        for inbound in inbounds:
            if inbound.port == port:
                return inbound
        return None
    
    async def get_inbound(self, inbound_id: int):
        await self.login()
        return await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )

    # ================= CLIENTS =================

    async def add_client(self, inbound_id: int, days: int):
        """Создаёт нового клиента.Возвращает dict:
        {
            uuid,
            email,
            expiry_time
        }
        """

        await self.login()

        client_uuid = str(uuid.uuid4())
        email = f"{client_uuid}@vpn"

        expiry_time = int(
            (datetime.utcnow() + timedelta(days=days)).timestamp() * 1000
        )

        await asyncio.to_thread(
            self.api.inbound.add,
            inbound_id,
            {
                "id": client_uuid,
                "email": email,
                "enable": True,
                "expiryTime": expiry_time,
            }
        )

        return {
            "uuid": client_uuid,
            "email": email,
            "expiry_time": expiry_time,
        }

    async def extend_client(self, inbound_id: int, email: str, days: int):
        """Реальное продление клиента в XUI"""

        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )

        if not inbound:
            raise Exception("Inbound не найден")

        clients = inbound.settings.clients
        found = False

        for client in clients:
            if client.email == email:
                client.expiryTime = int(
                    (datetime.utcnow() + timedelta(days=days)).timestamp() * 1000
                )
                found = True
                break

        if not found:
            raise Exception("Клиент не найден")

        await asyncio.to_thread(
            self.api.inbound.update,
            inbound_id,
            inbound.settings.dict()
        )

        return True

    async def remove_client(self, inbound_id: int, email: str):
        """Удаляет клиента из inbound"""

        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )

        if not inbound:
            raise Exception("Inbound не найден")

        clients = inbound.settings.clients
        new_clients = [c for c in clients if c.email != email]

        if len(new_clients) == len(clients):
            raise Exception("Клиент не найден")

        inbound.settings.clients = new_clients

        await asyncio.to_thread(
            self.api.inbound.update,
            inbound_id,
            inbound.settings.dict()
        )

        return True