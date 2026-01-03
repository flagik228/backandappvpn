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
    
    async def get_inbound_raw(self, inbound_id: int) -> dict:
        await self.login()

        def _req():
            r = self.api.client.get(
                f"/panel/api/inbounds/get/{inbound_id}"
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("success"):
                raise Exception(f"XUI error: {data}")
            return data["obj"]

        return await asyncio.to_thread(_req)

    # ————————— CLIENTS —————————

    async def add_client(self, inbound_id: int, days: int):
        """Добавляет нового клиента в inbound"""

        await self.login()

        # получаем inbound
        inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
        if not inbound:
            raise Exception("Inbound не найден")

        # новый UUID и email
        client_uuid = str(uuid.uuid4())
        email = f"{client_uuid}@vpn"

        # время истечения в мс
        expiry_time = int((datetime.utcnow() + timedelta(days=days)).timestamp() * 1000)

        # формируем клиентскую модель
        new_client = Client(
            id=client_uuid,
            email=email,
            enable=True,
            expiryTime=expiry_time
        )

        # добавляем клиента через api.client.add
        # py3xui ожидает список клиентов для добавления
        await asyncio.to_thread(self.api.client.add, inbound_id, [new_client])

        return {"uuid": client_uuid, "email": email, "expiry_time": expiry_time}

    async def extend_client(self, inbound_id: int, email: str, days: int):
        """Продлевает существующего клиента"""

        await self.login()

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
        if not inbound:
            raise Exception("Inbound не найден")

        # ищем в списке клиентов
        found = False
        for client in inbound.settings.clients or []:
            if client.email == email:
                # увеличиваем expiryTime
                client.expiryTime += days * 24 * 60 * 60 * 1000
                found = True
                break

        if not found:
            raise Exception("Клиент не найден")

        # обновляем клиента через API
        await asyncio.to_thread(self.api.client.update, client.id, client)

        return True

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