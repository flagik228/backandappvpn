from sqlalchemy import select, update, delete
from models import async_session, User, VPNKey, VPNSubscription, TypesVPN, CountriesVPN, ServersVPN, ReferralEarning
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
            raise ValueError("User not registered")

        server = await session.get(ServersVPN, int(server_id))
        if not server:
            raise ValueError("Server not found")

        api = OutlineAPI(server.api_url)
        key_data = api.create_key("VPN User")

        expires = datetime.utcnow() + timedelta(days=30)

        vpn_key = VPNKey(
            idUser=user.idUser,
            idServerVPN=server.idServerVPN,
            provider="outline",
            provider_key_id=key_data["id"],
            access_data=key_data["accessUrl"],
            expires_at=expires,
            is_active=True
        )
        session.add(vpn_key)
        await session.flush()

        subscription = VPNSubscription(
            idUser=user.idUser,
            vpn_key_id=vpn_key.id,
            started_at=datetime.utcnow(),
            expires_at=expires,
            status="active"
        )
        session.add(subscription)

        await session.commit()


async def renew_vpn_from_payload(payload: str):
    _, tg_id, key_id, months, _ = payload.split(":")

    async with async_session() as session:
        key = await session.get(VPNKey, int(key_id))
        key.expires_at += timedelta(days=30 * int(months))
        await session.commit()


# --- Пользователи ---
async def add_user(tg_id: int, user_role: str, referrer_id: int | None = None):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user:
            return user

        new_user = User(
            tg_id=tg_id,
            userRole=user_role,
            referrer_id=referrer_id
        )
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
        return new_user
    
# =======================
# --- REFERRAL SYSTEM ---
# =======================

async def get_referrals_count(tg_id: int) -> int:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return 0
        referrals = await session.scalars(select(User).where(User.referrer_id == user.idUser))
        return len(referrals.all())

async def get_referrals_list(tg_id: int):
    """
    Возвращает список приглашённых пользователей с базовой информацией
    """
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        referrals = await session.scalars(select(User).where(User.referrer_id == user.idUser))
        result = []
        for u in referrals:
            result.append({
                "tg_id": u.tg_id,
                "created_at": u.created_at.isoformat(),
                "trial_until": u.trial_until.isoformat() if u.trial_until else None
            })
        return result


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
    

# --- USERS ADMIN ---
async def admin_get_users():
    async with async_session() as session:
        users = await session.scalars(select(User))
        return [{"idUser": u.idUser, "tg_id": u.tg_id, "userRole": u.userRole} for u in users]

async def admin_add_user(tg_id: int, userRole: str):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user:
            return {"idUser": user.idUser, "tg_id": user.tg_id, "userRole": user.userRole}
        new_user = User(tg_id=tg_id, userRole=userRole)
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
        return {"idUser": new_user.idUser, "tg_id": new_user.tg_id, "userRole": new_user.userRole}

async def admin_update_user(user_id: int, tg_id: int, userRole: str):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        await session.execute(update(User).where(User.idUser == user_id).values(tg_id=tg_id, userRole=userRole))
        await session.commit()
        return {"status": "ok"}

async def admin_delete_user(user_id: int):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        await session.delete(user)
        await session.commit()
        return {"status": "ok"}
    
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
    

# --- VPN KEYS ADMIN ---
async def admin_get_keys():
    async with async_session() as session:
        keys = await session.scalars(select(VPNKey))
        return [{
            "id": k.id,
            "idUser": k.idUser,
            "idServerVPN": k.idServerVPN,
            "provider": k.provider,
            "provider_key_id": k.provider_key_id,
            "access_data": k.access_data,
            "expires_at": k.expires_at.isoformat(),
            "is_active": k.is_active
        } for k in keys]

async def admin_add_key(key):
    async with async_session() as session:
        user = await session.get(User, key.idUser)
        server = await session.get(ServersVPN, key.idServerVPN)
        if not user or not server:
            raise ValueError("User or Server not found")
        k = VPNKey(**key.dict())
        session.add(k)
        await session.commit()
        await session.refresh(k)
        return {"id": k.id}

async def admin_update_key(key_id: int, key):
    async with async_session() as session:
        k = await session.get(VPNKey, key_id)
        if not k:
            raise ValueError("Key not found")
        await session.execute(update(VPNKey).where(VPNKey.id == key_id).values(**key.dict()))
        await session.commit()
        return {"status": "ok"}

async def admin_delete_key(key_id: int):
    async with async_session() as session:
        k = await session.get(VPNKey, key_id)
        if not k:
            raise ValueError("Key not found")
        await session.delete(k)
        await session.commit()
        return {"status": "ok"}

# --- VPN SUBSCRIPTIONS ADMIN ---
async def admin_get_subscriptions():
    async with async_session() as session:
        subs = await session.scalars(select(VPNSubscription))
        return [{
            "id": s.id,
            "idUser": s.idUser,
            "vpn_key_id": s.vpn_key_id,
            "started_at": s.started_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
            "status": s.status
        } for s in subs]

async def admin_add_subscription(sub):
    async with async_session() as session:
        user = await session.get(User, sub.idUser)
        key = await session.get(VPNKey, sub.vpn_key_id)
        if not user or not key:
            raise ValueError("User or Key not found")
        s = VPNSubscription(**sub.dict())
        if not s.started_at:
            s.started_at = datetime.utcnow()
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return {"id": s.id}

async def admin_update_subscription(sub_id: int, sub):
    async with async_session() as session:
        s = await session.get(VPNSubscription, sub_id)
        if not s:
            raise ValueError("Subscription not found")
        await session.execute(update(VPNSubscription).where(VPNSubscription.id == sub_id).values(**sub.dict()))
        await session.commit()
        return {"status": "ok"}

async def admin_delete_subscription(sub_id: int):
    async with async_session() as session:
        s = await session.get(VPNSubscription, sub_id)
        if not s:
            raise ValueError("Subscription not found")
        await session.delete(s)
        await session.commit()
        return {"status": "ok"}

# --- REFERRAL EARNINGS ADMIN ---
async def admin_get_referral_earnings():
    async with async_session() as session:
        earnings = await session.scalars(select(ReferralEarning))
        return [{
            "id": e.id,
            "referrer_id": e.referrer_id,
            "referred_id": e.referred_id,
            "amount": e.amount,
            "created_at": e.created_at.isoformat()
        } for e in earnings]

async def admin_add_referral_earning(e):
    async with async_session() as session:
        referrer = await session.get(User, e.referrer_id)
        referred = await session.get(User, e.referred_id)
        if not referrer or not referred:
            raise ValueError("Referrer or referred user not found")
        earning = ReferralEarning(**e.dict())
        session.add(earning)
        await session.commit()
        await session.refresh(earning)
        return {"id": earning.id}

async def admin_delete_referral_earning(earning_id: int):
    async with async_session() as session:
        e = await session.get(ReferralEarning, earning_id)
        if not e:
            raise ValueError("Earning not found")
        await session.delete(e)
        await session.commit()
        return {"status": "ok"}