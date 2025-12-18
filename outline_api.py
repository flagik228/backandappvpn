import requests
from typing import Optional, List, Dict


class OutlineAPI:
    """
    Класс для работы с Outline Server API
    Документация: https://github.com/Jigsaw-Code/outline-server
    """

    def __init__(self, api_url: str, api_token: Optional[str] = None):
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token

    # =======================
    # --- INTERNAL REQUEST ---
    # =======================
    def _request(self, method: str, endpoint: str, data: Optional[dict] = None) -> Dict:
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        headers = {
            "Content-Type": "application/json"
        }

        # если позже захочешь авторизацию по токену
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        response = requests.request(
            method=method,
            url=url,
            json=data,
            headers=headers,
            timeout=10
        )

        response.raise_for_status()
        return response.json() if response.content else {}

    # =======================
    # --- ACCESS KEYS ---
    # =======================

    def list_keys(self) -> List[dict]:  # Получить список всех ключей на сервере
        return self._request("GET", "access-keys").get("accessKeys", [])

    def create_key(self, name: str = "VPN User") -> dict:   # Создать новый VPN ключ
        return self._request(
            "POST",
            "access-keys",
            data={"name": name}
        )

    def delete_key(self, key_id: str) -> dict:  # Удалить VPN ключ по ID
        return self._request(
            "DELETE",
            f"access-keys/{key_id}"
        )

    def update_key(self, key_id: str, name: Optional[str] = None) -> dict:  # Обновить имя VPN ключа
        data = {}
        if name:
            data["name"] = name

        return self._request(
            "PUT",
            f"access-keys/{key_id}",
            data=data
        )

    # =======================
    # --- SERVER INFO ---
    # =======================

    def get_server_info(self) -> dict:  # Информация о сервере Outline
        return self._request("GET", "server")

    def get_metrics(self) -> dict:  # Метрики сервера (если включены)
        return self._request("GET", "metrics")