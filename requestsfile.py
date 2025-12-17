from sqlalchemy import select, update, delete
from models import async_session, User, VPNKey, VPNSubscription, TypesVPN, CountriesVPN, ServersVPN
from outline_api import OutlineAPI
from typing import List
from datetime import datetime, timedelta



async def get_server_by_id(server_id: int):
    async with async_session() as session:
        s = await session.get(ServersVPN, server_id)
        if not s:
            return None
        return {
            "idServerVPN": s.idServerVPN,
            "nameVPN": s.nameVPN,
            "price": s.price,
            "api_url": s.api_url
        }


# активация впн после оплаты
async def activate_vpn_from_payload(payload: str):
    _, tg_id, server_id, _ = payload.split(":")

    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == int(tg_id)))
        if not user:
            user = User(tg_id=int(tg_id))
            session.add(user)
            await session.commit()

        server = await session.get(ServersVPN, int(server_id))
        api = OutlineAPI(server.api_url)

        key_data = api.create_key("VPN User")

        vpn_key = VPNKey(
            idUser=user.idUser,
            idServerVPN=server.idServerVPN,
            provider="outline",
            provider_key_id=key_data["id"],
            access_data=key_data["accessUrl"],
            expires_at=datetime.utcnow() + timedelta(days=30)
        )

        session.add(vpn_key)
        await session.commit()


async def renew_vpn_from_payload(payload: str):
    _, tg_id, key_id, months, _ = payload.split(":")

    async with async_session() as session:
        key = await session.get(VPNKey, int(key_id))
        key.expires_at += timedelta(days=30 * int(months))
        await session.commit()




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
                "vpn_key_id": key.id,
                "server_id": key.idServerVPN,
                "serverName": (await session.get(ServersVPN, key.idServerVPN)).nameVPN,
                "access_data": key.access_data,
                "expires_at": key.expires_at.isoformat(),
                "is_active": key.is_active
            })
        return result
    
    
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
