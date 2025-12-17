from sqlalchemy import select, update, delete
from models import async_session, User, VPNKey, VPNSubscription, TypesVPN, CountriesVPN, ServersVPN
from outline_api import OutlineAPI
from typing import List
from datetime import datetime, timedelta


# --- Пользователи ---
async def add_user(tg_id: int, user_role: str):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user:
            return user

        new_user = User(tg_id=tg_id, userRole=user_role)
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
        return new_user


# --- Серверы VPN ---
async def get_servers() -> List[dict]:
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN).where(ServersVPN.is_active == True))
        return [
            {
                "idServerVPN": s.idServerVPN,
                "nameVPN": s.nameVPN,
                "price": s.price,
                "max_conn": s.max_conn,
                "now_conn": s.now_conn,
                "server_ip": s.server_ip,
                "api_url": s.api_url
            } for s in servers
        ]


# --- Покупка VPN ---
async def buy_vpn(tg_id: int, server_id: int, outline_api_url: str) -> dict:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("Пользователь не найден")

        server = await session.scalar(select(ServersVPN).where(ServersVPN.idServerVPN == server_id))
        if not server or not server.is_active:
            raise Exception("Сервер не найден или неактивен")

        # Создаём ключ через Outline API
        outline_api = OutlineAPI(api_url=outline_api_url)
        key_data = outline_api.create_key(name=f"User {tg_id}")

        expires_at = datetime.utcnow() + timedelta(days=30)

        # VPNKey
        vpn_key = VPNKey(
            idUser=user.idUser,
            idServerVPN=server.idServerVPN,
            provider="outline",
            provider_key_id=key_data["accessKey"]["id"],
            access_data=key_data["accessUrl"],
            expires_at=expires_at,
            is_active=True
        )
        session.add(vpn_key)
        await session.commit()
        await session.refresh(vpn_key)

        # VPNSubscription
        subscription = VPNSubscription(
            idUser=user.idUser,
            vpn_key_id=vpn_key.id,
            started_at=datetime.utcnow(),
            expires_at=expires_at,
            status="active"
        )
        session.add(subscription)
        await session.commit()
        await session.refresh(subscription)

        return {"vpn_key": vpn_key.access_data, "expires_at": vpn_key.expires_at.isoformat()}


# --- Список VPN пользователя ---
async def get_my_vpns(tg_id: int) -> List[dict]:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        subscriptions = await session.scalars(
            select(VPNSubscription, VPNKey)
            .join(VPNKey, VPNSubscription.vpn_key_id == VPNKey.id)
            .where(VPNSubscription.idUser == user.idUser)
        )

        result = []
        for sub, key in subscriptions:
            result.append({
                "server_id": key.idServerVPN,
                "access_data": key.access_data,
                "expires_at": key.expires_at.isoformat(),
                "is_active": key.is_active
            })
        return result


# --- Продление VPN ---
async def renew_vpn(tg_id: int, vpn_key_id: int, months: int = 1) -> dict:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("Пользователь не найден")

        vpn_key = await session.scalar(select(VPNKey).where(VPNKey.id == vpn_key_id, VPNKey.idUser == user.idUser))
        if not vpn_key:
            raise Exception("VPN ключ не найден")

        # Новая дата окончания = старая дата или сейчас, + месяцы
        new_expiry = max(vpn_key.expires_at, datetime.utcnow()) + timedelta(days=30 * months)

        # Обновляем VPNKey
        await session.execute(update(VPNKey).where(VPNKey.id == vpn_key.id).values(expires_at=new_expiry))

        # Обновляем последнюю подписку
        await session.execute(update(VPNSubscription)
                              .where(VPNSubscription.vpn_key_id == vpn_key.id)
                              .values(expires_at=new_expiry, status="active"))

        await session.commit()
        return {"vpn_key": vpn_key.access_data, "new_expires_at": new_expiry.isoformat()}
    
    
    
# =======================
# --- TYPES VPN ---
# =======================
async def admin_get_types():
    async with async_session() as session:
        types = await session.scalars(select(TypesVPN))
        return [{"idTypeVPN": t.idTypeVPN, "nameType": t.nameType, "descriptionType": t.descriptionType} for t in types]

async def admin_add_type(nameType: str, descriptionType: str):
    if not nameType or not descriptionType:
        raise ValueError("nameType и descriptionType не могут быть пустыми")

    async with async_session() as session:
        t = TypesVPN(nameType=nameType, descriptionType=descriptionType)
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return {"idTypeVPN": t.idTypeVPN, "nameType": t.nameType, "descriptionType": t.descriptionType}

async def admin_update_type(type_id: int, nameType: str, descriptionType: str):
    if not nameType or not descriptionType:
        raise ValueError("nameType и descriptionType не могут быть пустыми")

    async with async_session() as session:
        type_obj = await session.get(TypesVPN, type_id)
        if not type_obj:
            raise ValueError(f"TypeVPN с id {type_id} не найден")

        await session.execute(update(TypesVPN).where(TypesVPN.idTypeVPN == type_id).values(
            nameType=nameType,
            descriptionType=descriptionType
        ))
        await session.commit()
        return {"status": "ok"}

async def admin_delete_type(type_id: int):
    async with async_session() as session:
        type_obj = await session.get(TypesVPN, type_id)
        if not type_obj:
            raise ValueError(f"TypeVPN с id {type_id} не найден")

        await session.delete(type_obj)
        await session.commit()
        return {"status": "ok"}

# =======================
# --- COUNTRIES ---
# =======================
async def admin_get_countries():
    async with async_session() as session:
        countries = await session.scalars(select(CountriesVPN))
        return [{"idCountry": c.idCountry, "nameCountry": c.nameCountry} for c in countries]

async def admin_add_country(nameCountry: str):
    if not nameCountry:
        raise ValueError("nameCountry не может быть пустым")

    async with async_session() as session:
        c = CountriesVPN(nameCountry=nameCountry)
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {"idCountry": c.idCountry, "nameCountry": c.nameCountry}

async def admin_update_country(country_id: int, nameCountry: str):
    if not nameCountry:
        raise ValueError("nameCountry не может быть пустым")

    async with async_session() as session:
        country_obj = await session.get(CountriesVPN, country_id)
        if not country_obj:
            raise ValueError(f"CountryVPN с id {country_id} не найден")

        await session.execute(update(CountriesVPN).where(CountriesVPN.idCountry == country_id).values(
            nameCountry=nameCountry
        ))
        await session.commit()
        return {"status": "ok"}

async def admin_delete_country(country_id: int):
    async with async_session() as session:
        country_obj = await session.get(CountriesVPN, country_id)
        if not country_obj:
            raise ValueError(f"CountryVPN с id {country_id} не найден")

        await session.delete(country_obj)
        await session.commit()
        return {"status": "ok"}

# =======================
# --- SERVERS ---
# =======================
async def admin_get_servers() -> List[dict]:
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN))
        result = []
        for s in servers:
            type_obj = await session.get(TypesVPN, s.idTypeVPN)
            country_obj = await session.get(CountriesVPN, s.idCountry)
            result.append({
                "idServerVPN": s.idServerVPN,
                "nameVPN": s.nameVPN,
                "price": s.price,
                "max_conn": s.max_conn,
                "now_conn": s.now_conn,
                "server_ip": s.server_ip,
                "api_url": s.api_url,
                "api_token": s.api_token,
                "is_active": s.is_active,
                "idTypeVPN": s.idTypeVPN,
                "idCountry": s.idCountry,
                "typeName": type_obj.nameType if type_obj else "",
                "countryName": country_obj.nameCountry if country_obj else ""
            })
        return result

async def admin_add_server(server):
    async with async_session() as session:
        # проверяем, что idTypeVPN существует
        type_obj = await session.get(TypesVPN, server.idTypeVPN)
        if not type_obj:
            raise ValueError(f"TypeVPN с id {server.idTypeVPN} не найден")

        # проверяем, что idCountry существует
        country_obj = await session.get(CountriesVPN, server.idCountry)
        if not country_obj:
            raise ValueError(f"CountryVPN с id {server.idCountry} не найден")

        s = ServersVPN(
            nameVPN=server.nameVPN,
            price=server.price,
            max_conn=server.max_conn,
            server_ip=server.server_ip,
            api_url=server.api_url,
            api_token=server.api_token,
            idTypeVPN=server.idTypeVPN,
            idCountry=server.idCountry,
            is_active=server.is_active
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return {"idServerVPN": s.idServerVPN, "nameVPN": s.nameVPN}

async def admin_update_server(server_id: int, server):
    async with async_session() as session:
        # проверка существования сервера
        existing = await session.get(ServersVPN, server_id)
        if not existing:
            raise ValueError(f"Сервер с id {server_id} не найден")

        # проверяем TypeVPN
        type_obj = await session.get(TypesVPN, server.idTypeVPN)
        if not type_obj:
            raise ValueError(f"TypeVPN с id {server.idTypeVPN} не найден")

        # проверяем CountryVPN
        country_obj = await session.get(CountriesVPN, server.idCountry)
        if not country_obj:
            raise ValueError(f"CountryVPN с id {server.idCountry} не найден")

        await session.execute(update(ServersVPN).where(ServersVPN.idServerVPN == server_id).values(
            nameVPN=server.nameVPN,
            price=server.price,
            max_conn=server.max_conn,
            server_ip=server.server_ip,
            api_url=server.api_url,
            api_token=server.api_token,
            idTypeVPN=server.idTypeVPN,
            idCountry=server.idCountry,
            is_active=server.is_active
        ))
        await session.commit()
        return {"status": "ok"}

async def admin_delete_server(server_id: int):
    async with async_session() as session:
        await session.execute(delete(ServersVPN).where(ServersVPN.idServerVPN == server_id))
        await session.commit()
        return {"status": "ok"}
