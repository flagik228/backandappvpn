import httpx
from datetime import datetime, timedelta

class XUIApi:
    """
    Класс для работы с 3xui (VLESS) через REST API
    """
    def __init__(self, api_url: str, username: str, password: str):
        self.api_url = api_url.rstrip("/")
        self.auth = (username, password)
        self.client = httpx.AsyncClient(auth=self.auth, timeout=10, verify=False)

    # ====== INBOUND ======
    async def get_inbounds(self):
        r = await self.client.get(f"{self.api_url}/api/v1/inbound")
        r.raise_for_status()
        return r.json()

    async def get_inbound_by_port(self, port: int):
        inbounds = await self.get_inbounds()
        for inbound in inbounds.get("data", []):
            if inbound.get("port") == port:
                return inbound
        return None

    # ====== CLIENTS ======
    async def add_client(self, inbound_port: int, email: str, remark: str = ""):
        """
        Создаём нового клиента (ключ)
        """
        data = {
            "port": inbound_port,
            "email": email,
            "remark": remark
        }
        r = await self.client.post(f"{self.api_url}/api/v1/client", json=data)
        r.raise_for_status()
        return r.json()

    async def remove_client(self, inbound_port: int, client_id: str):
        """
        Удаляем клиента по ID
        """
        data = {"port": inbound_port, "id": client_id}
        r = await self.client.delete(f"{self.api_url}/api/v1/client", json=data)
        r.raise_for_status()
        return r.json()

    async def get_clients(self, inbound_port: int):
        r = await self.client.get(f"{self.api_url}/api/v1/client?port={inbound_port}")
        r.raise_for_status()
        return r.json()

    # ====== HELPERS ======
    async def close(self):
        await self.client.aclose()
