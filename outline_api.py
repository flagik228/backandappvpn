import requests
from typing import Optional, List, Dict

"""Класс для работы с Outline Server API."""
class OutlineAPI:
    def __init__(self, api_url: str):
        # Убираем лишний слэш в конце
        self.api_url = api_url.rstrip('/')

    """Вспомогательная функция для запросов к API Outline."""
    def _request(self, method: str, endpoint: str, data: Optional[dict] = None) -> Dict:
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        headers = {'Content-Type': 'application/json'}
        response = requests.request(method, url, json=data, headers=headers, timeout=10)
        response.raise_for_status()  # выбросит исключение, если ошибка HTTP
        return response.json()

    # -------------------- Основные методы --------------------

    """Получить список всех ключей на сервере."""
    def list_keys(self) -> List[dict]:
        return self._request("GET", "access-keys")

    """Создать новый VPN ключ. Возвращает JSON с данными ключа."""
    def create_key(self, name: str = "VPN User") -> dict:
        data = {"name": name}
        return self._request("POST", "access-keys", data)

    """Удалить ключ по ID."""
    def delete_key(self, key_id: str) -> dict:
        
        return self._request("DELETE", f"access-keys/{key_id}")

    """Обновить имя ключа."""
    def update_key(self, key_id: str, name: Optional[str] = None) -> dict:
        data = {}
        if name:
            data["name"] = name
        return self._request("PUT", f"access-keys/{key_id}", data)
