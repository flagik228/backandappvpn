# Руководство: Добавление нового VPN-сервера в ArtCry VPN

## Часть 1: Выбор VPS-сервера

### Рекомендуемые локации (отличные от Финляндии)

| Локация | Плюсы | Пример провайдеров |
|---------|-------|-------------------|
| **Нидерланды** | Низкая задержка в Европе, нет блокировок VPN, хорошая ценовая политика | VPS.one (~€3.5/мес), Hostkey (€3/мес), Hetzner |
| **Германия** | Надёжная инфраструктура, Hetzner | Hetzner Cloud (€4-12/мес) |
| **Польша** | Дешево, стабильно | Various |
| **США** | Для доступа к американскому контенту | Vultr, DigitalOcean |
| **Сингапур** | Для азиатского рынка | Vultr, Linode |

### Минимальные требования для 3x-ui + Xray

- **CPU:** 1 vCPU  
- **RAM:** 1 GB (рекомендуется 2 GB для стабильности)  
- **Диск:** 15–20 GB SSD  
- **Сеть:** безлимитный трафик или минимум 1 TB/месяц  
- **ОС:** Ubuntu 20.04/22.04 или Debian 11/12  

### Важные критерии при выборе провайдера

1. **Нет блокировок VPN** — провайдеры вроде Hetzner, OVH, Hostkey обычно нормально относятся к VPN.
2. **IPv4** — желательно выделенный IPv4 (для IP-сертификатов Let's Encrypt).
3. **Порты 80, 443** — должны быть открыты для Let's Encrypt (для SSL).

---

## Часть 2: Установка 3x-ui на новый сервер

### Шаг 1: Подключение к серверу

```bash
ssh root@YOUR_SERVER_IP
```

### Шаг 2: Установка 3x-ui (одна команда)

```bash
bash <(curl -Ls https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh)
```

Скрипт:
- Установит 3x-ui и Xray
- Предложит задать порт панели
- Предложит настроить SSL (Let's Encrypt для домена или IP)

### Шаг 3: Что важно при установке

1. **Порт панели** — можно оставить случайный или задать свой (например 2053). Запомните его.
2. **SSL-сертификат** — выберите:
   - **Вариант 2 (IP)** — если используете только IP (нужен открытый порт 80).
   - **Вариант 1 (домен)** — если есть домен, указывающий на сервер.
3. **Логин и пароль** — сохраните сгенерированные или введите свои.

### Шаг 4: Проверка доступа к панели

```
https://YOUR_SERVER_IP:YOUR_PORT/WebBasePath
```

(WebBasePath покажет скрипт после установки.)

---

## Часть 3: Настройка Inbound в 3x-ui

Чтобы мини-приложение могло создавать VPN, нужен **VLESS + Reality** inbound (или тот же протокол, что на финском сервере).

### 3.1. Создание Inbound через веб-панель

1. Войдите в панель 3x-ui.
2. **Inbounds** → **Add Inbound**.
3. Задайте настройки (пример для VLESS Reality):

| Параметр | Значение (пример) |
|----------|-------------------|
| Remark | Finland / Netherlands / etc. |
| Protocol | VLESS |
| Port | 443 (или любой свободный порт) |
| Network | tcp |
| Security | reality |
| Reality Settings | оставьте дефолтные или сгенерируйте новые SNI, fingerprint и т.п. |

4. **Включите опцию "Subscription"** — это важно для генерации subscription URL.
5. **Subscription Port** — обычно 2096 (или другой, который потом укажете в `subscription_port`).

### 3.2. Запись нужных параметров

После создания inbound запишите:

- **Port** (порт Xray) → это `inbound_port` в БД.
- **Subscription Port** (порт подписок) → это `subscription_port` в БД (по умолчанию 2096).

---

## Часть 4: Добавление сервера в мини-приложение

### 4.1. Данные, которые нужны из панели

| Поле в БД | Откуда взять |
|-----------|--------------|
| `server_ip` | IP вашего нового сервера |
| `api_url` | `https://IP:ПОРТ_ПАНЕЛИ/WebBasePath` (без `/` в конце) |
| `xui_username` | Логин панели 3x-ui |
| `xui_password` | Пароль панели 3x-ui |
| `inbound_port` | Порт inbound (например 443) |
| `subscription_port` | Порт subscription (обычно 2096) |

### 4.2. Через Admin API (POST /api/admin/servers)

```json
{
  "nameVPN": "Netherlands",
  "price_usdt": "0.5",
  "max_conn": 500,
  "server_ip": "YOUR_NEW_SERVER_IP",
  "api_url": "https://YOUR_NEW_SERVER_IP:2053/AbCdEfGh123456",
  "api_token": "",
  "xui_username": "admin",
  "xui_password": "your_secure_password",
  "inbound_port": 443,
  "subscription_port": 2096,
  "idTypeVPN": 1,
  "idCountry": 2,
  "is_active": true
}
```

**Важно:**
- `idTypeVPN` — ID типа VPN из таблицы `types_vpn` (1 = vless или как у финского сервера).
- `idCountry` — ID страны из таблицы `countries_vpn` (если нет Нидерландов — добавьте через `/api/admin/countries`).
- `api_url` должен быть доступен с сервера, где крутится бэкенд (если бэкенд за NAT — проверьте доступность).

### 4.3. Добавление тарифов

После создания сервера добавьте тарифы через `POST /api/admin/tariffs`:

```json
{
  "server_id": 2,
  "days": 7,
  "price_tarif": "0.5",
  "is_active": true
}
```

(Создайте тарифы для 1, 7, 14, 30 дней — как у финского сервера.)

### 4.4. Если используете Bundle (все сервера)

Добавьте новый сервер в Bundle Plan через админку:

- Откройте Bundle Plan.
- В список `server_ids` добавьте ID нового сервера.

---

## Часть 5: Проверка

1. **Доступность панели с бэкенда:**
   ```bash
   curl -k https://YOUR_NEW_SERVER_IP:PORT/WebBasePath/
   ```
   Должен вернуться HTML логина.

2. **В мини-приложении:**
   - Откройте список серверов — должен появиться новый.
   - Создайте тестовую подписку (1 день) и проверьте subscription URL в клиенте (v2rayNG, Nekobox и т.п.).

3. **Если subscription URL не работает:**
   - Убедитесь, что порт `subscription_port` открыт в firewall (`ufw allow 2096`).
   - Проверьте, что inbound создан с опцией Subscription.

---

## Часть 6: Firewall

```bash
# Открыть порты
ufw allow 443/tcp    # Xray
ufw allow 2096/tcp   # Subscription
ufw allow 80/tcp     # Let's Encrypt (временно при выдаче сертификата)
ufw allow YOUR_PANEL_PORT/tcp
ufw enable
ufw status
```

---

## Краткий чеклист

- [ ] Арендован VPS (Нидерланды/Германия/другое)
- [ ] Установлен 3x-ui (`bash <(curl -Ls ...)`)
- [ ] Настроен SSL (Let's Encrypt)
- [ ] Создан Inbound с Subscription
- [ ] Записаны: api_url, xui_username, xui_password, inbound_port, subscription_port
- [ ] Добавлена страна (если нужно) через `/api/admin/countries`
- [ ] Добавлен сервер через `/api/admin/servers`
- [ ] Добавлены тарифы через `/api/admin/tariffs`
- [ ] Новый сервер добавлен в Bundle Plan (если используется)
- [ ] Firewall настроен
- [ ] Тестовая покупка VPN прошла успешно
