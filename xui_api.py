import uuid
import asyncio
import requests
import urllib3
from datetime import datetime, timedelta
from py3xui import Api
from py3xui.client.client import Client  # корректный импорт клиента


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_old_request = requests.Session.request

def _patched_request(self, method, url, **kwargs):
    kwargs["verify"] = False
    return _old_request(self, method, url, **kwargs)

requests.Session.request = _patched_request


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
    async def add_client(self, inbound_id: int, email: str, days: int, sub_id: str | None = None):
        await self.login()

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
        if not inbound:
            raise Exception("Inbound не найден")

        client_uuid = str(uuid.uuid4())
        expiry_time = int((datetime.utcnow() + timedelta(days=days)).timestamp() * 1000)

        try:
            new_client = Client(
                id=client_uuid,
                email=email,
                enable=True,
                expiry_time=expiry_time,
                sub_id=sub_id,
                limit_ip=2
            )
        except TypeError:
            new_client = Client(
                id=client_uuid,
                email=email,
                enable=True,
                expiry_time=expiry_time
            )
            if sub_id:
                try:
                    setattr(new_client, "sub_id", sub_id)
                    setattr(new_client, "subId", sub_id)
                except Exception:
                    pass
            try:
                setattr(new_client, "limit_ip", 2)
                setattr(new_client, "limitIp", 2)
            except Exception:
                pass

        await asyncio.to_thread(self.api.client.add, inbound_id, [new_client])

        client_sub_id = None
        if sub_id:
            client_sub_id = sub_id
        else:
            try:
                fetched = await asyncio.to_thread(self.api.client.get_by_email, email)
                for key in ("sub_id", "subId", "subid"):
                    client_sub_id = getattr(fetched, key, None)
                    if client_sub_id:
                        break
                if not client_sub_id and hasattr(fetched, "model_dump"):
                    data = fetched.model_dump()
                    client_sub_id = data.get("sub_id") or data.get("subId")
                if not client_sub_id and hasattr(fetched, "dict"):
                    data = fetched.dict()
                    client_sub_id = data.get("sub_id") or data.get("subId")
            except Exception:
                client_sub_id = None
        if not client_sub_id:
            try:
                inbound = await asyncio.to_thread(self.api.inbound.get_by_id, inbound_id)
                for c in inbound.settings.clients or []:
                    if c.email == email:
                        for key in ("sub_id", "subId", "subid"):
                            client_sub_id = getattr(c, key, None)
                            if client_sub_id:
                                break
                        if not client_sub_id and hasattr(c, "model_dump"):
                            data = c.model_dump()
                            client_sub_id = data.get("sub_id") or data.get("subId")
                        if not client_sub_id and hasattr(c, "dict"):
                            data = c.dict()
                            client_sub_id = data.get("sub_id") or data.get("subId")
                        break
            except Exception:
                client_sub_id = None

        return {
            "uuid": client_uuid,
            "email": email,
            "expiry_time": expiry_time,
            "sub_id": client_sub_id
        }


    async def extend_client(self, inbound_id: int, client_email: str, days: int, sub_id: str | None = None):
        await self.login()

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id,inbound_id)
        if not inbound:
            raise Exception("Inbound not found")

        old_client = None
        for c in inbound.settings.clients or []:
            if c.email == client_email:
                old_client = c
                break

        if not old_client:
            raise Exception("Client not found")

        # считаем новое время
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        add_ms = days * 86400000

        old_expiry = old_client.expiry_time or 0
        new_expiry = (
            old_expiry + add_ms
            if old_expiry > now_ms
            else now_ms + add_ms
        )

        client_uuid = old_client.id
        if not sub_id:
            for key in ("sub_id", "subId", "subid"):
                sub_id = getattr(old_client, key, None)
                if sub_id:
                    break
            if not sub_id and hasattr(old_client, "model_dump"):
                data = old_client.model_dump()
                sub_id = data.get("sub_id") or data.get("subId")
            if not sub_id and hasattr(old_client, "dict"):
                data = old_client.dict()
                sub_id = data.get("sub_id") or data.get("subId")

        # удаляем клиента
        inbound.settings.clients = [
            c for c in inbound.settings.clients
            if c.email != client_email
        ]

        await asyncio.to_thread(self.api.inbound.update,inbound_id,inbound)
        await asyncio.sleep(0.3)

        # создаём нового (С ТЕМ ЖЕ UUID)
        try:
            new_client = Client(
                id=client_uuid,
                email=client_email,
                enable=True,
                expiry_time=new_expiry,
                total_gb=0,
                up=0,
                down=0,
                limit_ip=2,
                sub_id=sub_id
            )
        except TypeError:
            new_client = Client(
                id=client_uuid,
                email=client_email,
                enable=True,
                expiry_time=new_expiry,
                total_gb=0,
                up=0,
                down=0
            )
            try:
                setattr(new_client, "limit_ip", 2)
                setattr(new_client, "limitIp", 2)
                if sub_id:
                    setattr(new_client, "sub_id", sub_id)
                    setattr(new_client, "subId", sub_id)
            except Exception:
                pass

        inbound.settings.clients.append(new_client)

        await asyncio.to_thread(self.api.inbound.update,inbound_id,inbound)

        return {"email": client_email,"new_expiry": new_expiry, "sub_id": sub_id}


    async def remove_client(self, inbound_id: int, client_uuid: str):
        await self.login()

        inbound = await asyncio.to_thread(self.api.inbound.get_by_id,inbound_id)

        for client in inbound.settings.clients or []:
            if client.id == client_uuid:
                await asyncio.to_thread(
                    self.api.client.delete,
                    inbound_id,
                    client.id
                )
                return True

        raise Exception("Client not found")