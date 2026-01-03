import httpx
from datetime import datetime, timedelta
from py3xui import Api
import uuid
import asyncio
# from py3xui.models import Client
from py3xui.client.client import Client


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
        
    
    async def close(self):
        # py3xui не требует явного close, но метод оставляем
        pass

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
        """Создаёт нового клиента в inbound Возвращает:
        {uuid,email,expiry_time}"""
        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )

        if not inbound:
            raise Exception("Inbound не найден")

        client_uuid = str(uuid.uuid4())
        email = f"{client_uuid}@vpn"

        expiry_time = int(
            (datetime.utcnow() + timedelta(days=days)).timestamp() * 1000
        )

        clients = inbound.settings.clients or []

        clients.append(
        Client(
            id=client_uuid,
            email=email,
            enable=True,
            expiryTime=expiry_time
        ))

        inbound.settings.clients = clients

        # 🔥 ЕДИНСТВЕННО ПРАВИЛЬНЫЙ СПОСОБ
        await asyncio.to_thread(
            self.api.inbound.update,
            inbound_id,
            inbound.settings.dict()
        )

        return {
            "uuid": client_uuid,
            "email": email,
            "expiry_time": expiry_time,
        }
        

    async def extend_client(self, inbound_id: int, email: str, days: int):
        """Продление существующего клиента"""

        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )

        if not inbound:
            raise Exception("Inbound не найден")

        found = False
        for client in inbound.settings.clients:
            if client.email == email:
                client.expiryTime += days * 24 * 60 * 60 * 1000
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
        """Удаление клиента"""

        await self.login()

        inbound = await asyncio.to_thread(
            self.api.inbound.get_by_id,
            inbound_id
        )

        if not inbound:
            raise Exception("Inbound не найден")

        new_clients = [
            c for c in inbound.settings.clients
            if c.email != email
        ]

        if len(new_clients) == len(inbound.settings.clients):
            raise Exception("Клиент не найден")

        inbound.settings.clients = new_clients

        await asyncio.to_thread(
            self.api.inbound.update,
            inbound_id,
            inbound.settings.dict()
        )

        return True