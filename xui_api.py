import httpx
from datetime import datetime, timedelta


class XUIApi:
    """
    Полностью совместимый API-клиент для 3x-ui 2.x/3.x
    """

    def __init__(self, api_url: str, username: str, password: str):
        self.api_url = api_url.rstrip("/")
        self.username = username
        self.password = password
        self.client = httpx.AsyncClient(
            timeout=15,
            verify=False,
            follow_redirects=True
        )
        self._logged_in = False

    # ===== AUTH =====
    async def login(self):
        if self._logged_in:
            return

        resp = await self.client.post(
            f"{self.api_url}/login",
            data={"username": self.username, "password": self.password}
        )
        resp.raise_for_status()
        result = resp.json()

        if not result.get("success"):
            raise Exception(f"Login failed: {result.get('msg')}")

        self._logged_in = True

    # ===== INBOUNDS =====
    async def get_inbounds(self):
        await self.login()
        resp = await self.client.get(f"{self.api_url}/xui/inbound/list")
        resp.raise_for_status()
        data = resp.json()
        inbounds = data.get("obj") or []
        return inbounds

    async def get_inbound_by_port(self, port: int):
        inbounds = await self.get_inbounds()
        for inbound in inbounds:
            if inbound.get("port") == port:
                return inbound
        return None

    # ===== CLIENTS =====
    async def add_client(self, inbound_id: int, email: str, days: int):
        await self.login()
        expire_time = int((datetime.utcnow() + timedelta(days=days)).timestamp() * 1000)

        # Получаем текущих клиентов, чтобы не перезаписывать существующих
        resp = await self.client.get(f"{self.api_url}/xui/inbound/get/{inbound_id}")
        resp.raise_for_status()
        inbound = resp.json().get("obj")
        if not inbound:
            raise Exception("Inbound не найден")
        settings = inbound.get("settings", {})
        clients = settings.get("clients", [])

        # Проверяем, нет ли уже клиента с таким email
        if any(c.get("email") == email for c in clients):
            raise Exception(f"Клиент {email} уже существует")

        clients.append({"email": email, "enable": True, "expiryTime": expire_time})

        payload = {
            "id": inbound_id,
            "settings": {"clients": clients}
        }

        resp = await self.client.post(f"{self.api_url}/xui/inbound/update", json=payload)
        resp.raise_for_status()
        result = resp.json()

        if not result.get("success"):
            raise Exception(f"Не удалось добавить клиента: {result.get('msg')}")

        return result

    async def get_clients(self, inbound_id: int):
        await self.login()
        resp = await self.client.get(f"{self.api_url}/xui/inbound/get/{inbound_id}")
        resp.raise_for_status()
        inbound = resp.json().get("obj")
        if not inbound:
            return []
        settings = inbound.get("settings", {})
        return settings.get("clients", [])

    async def remove_client(self, inbound_id: int, email: str):
        await self.login()
        resp = await self.client.get(f"{self.api_url}/xui/inbound/get/{inbound_id}")
        resp.raise_for_status()
        inbound = resp.json().get("obj")
        if not inbound:
            raise Exception("Inbound не найден")

        settings = inbound.get("settings", {})
        clients = settings.get("clients", [])
        new_clients = [c for c in clients if c.get("email") != email]

        if len(new_clients) == len(clients):
            raise Exception("Клиент не найден")

        payload = {"id": inbound_id, "settings": {"clients": new_clients}}
        resp = await self.client.post(f"{self.api_url}/xui/inbound/update", json=payload)
        resp.raise_for_status()
        return True

    async def extend_client(self, inbound_id: int, email: str, days: int):
        await self.login()
        resp = await self.client.get(f"{self.api_url}/xui/inbound/get/{inbound_id}")
        resp.raise_for_status()
        inbound = resp.json().get("obj")
        if not inbound:
            raise Exception("Inbound не найден")

        settings = inbound.get("settings", {})
        clients = settings.get("clients", [])

        found = False
        for c in clients:
            if c.get("email") == email:
                c["expiryTime"] = int((datetime.utcnow() + timedelta(days=days)).timestamp() * 1000)
                found = True
                break

        if not found:
            raise Exception("Клиент не найден")

        payload = {"id": inbound_id, "settings": {"clients": clients}}
        resp = await self.client.post(f"{self.api_url}/xui/inbound/update", json=payload)
        resp.raise_for_status()
        return True

    async def close(self):
        await self.client.aclose()
