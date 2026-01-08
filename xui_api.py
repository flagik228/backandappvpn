import uuid
import asyncio
from datetime import datetime, timedelta
from py3xui import Api
from py3xui.client.client import Client  # корректный импорт клиента

class XUIApi:
    """API-обёртка над py3xui, совместимая с 3x-ui 2.x/3.x"""

    def __init__(self, api_url: str, username: str, password: str):
        # py3xui Api работает и синхронно, и асинхронно через httpx
        self.api = Api(
            host=api_url,
            username=username,
            password=password
        )
        # если у тебя самоподписанный сертификат
        self.api.client.verify = False
        self._logged_in = False

    async def login(self):
        if not self._logged_in:
            # py3xui Api.login — синхронный, поэтому запускаем в потоке
            await asyncio.to_thread(self.api.login)
            self._logged_in = True


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
            expiryTime=expiry_time
        )

        await asyncio.to_thread(self.api.client.add, inbound_id, [new_client])

        return {
            "uuid": client_uuid,
            "email": email,
            "expiry_time": expiry_time
        }


    async def extend_client(self, inbound_id: int, email: str, days: int):
        await self.login()

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
        if not inbound:
            raise Exception("Inbound не найден")

        now_ms = int(datetime.utcnow().timestamp() * 1000)
        clients = inbound.settings.clients or []

        for client in clients:
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
        """Удаляет клиента"""

        await self.login()

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
        if not inbound:
            raise Exception("Inbound не найден")

        # ищем того, кого удаляем
        for client in inbound.settings.clients or []:
            if client.email == email:
                # удаляем через api.client.delete
                await asyncio.to_thread(self.api.client.delete, inbound_id, client.id)
                return True

        raise Exception("Клиент не найден")