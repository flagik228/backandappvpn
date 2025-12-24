from sqlalchemy import select, update, delete
from models import (async_session, User, UserWallet, WalletTransaction, VPNKey,
    VPNSubscription, TypesVPN, CountriesVPN, ServersVPN, Tariff, ExchangeRate, Order, Payment
)
from typing import List
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import func

from outline_api import OutlineAPI


# =======================
# --- USERS ---
# =======================
async def add_user(tg_id: int, user_role: str = "user", referrer_id: int | None = None):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user:
            return user
        user = User(
            tg_id=tg_id,
            userRole=user_role,
            referrer_id=referrer_id
        )
        session.add(user)
        await session.flush()

        wallet = UserWallet(user_id=user.idUser, balance_usdt=Decimal("0.00"))
        session.add(wallet)
        await session.commit()
        await session.refresh(user)
        return user


async def get_user_wallet(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return None

        wallet = await session.scalar(select(UserWallet).where(UserWallet.user_id == user.idUser))
        return {
            "balance_usdt": str(wallet.balance_usdt)
        }

# =======================
# --- REFERRALS ---
# =======================
async def get_referrals_count(tg_id: int) -> int:
    async with async_session() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == tg_id)
        )
        if not user:
            return 0

        count = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.referrer_id == user.idUser)
        )
        return count or 0


async def get_referrals_list(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        referrals = await session.scalars(select(User).where(User.referrer_id == user.idUser))
        return [{
            "tg_id": r.tg_id,
            "created_at": r.created_at.isoformat()
        } for r in referrals]

# =======================
# --- SERVERS ---
# =======================
async def get_servers() -> List[dict]:
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN).where(ServersVPN.is_active == True))
        return [{
            "idServerVPN": s.idServerVPN,
            "nameVPN": s.nameVPN,
            "price_usdt": str(s.price_usdt),
            "max_conn": s.max_conn,
            "now_conn": s.now_conn,
            "server_ip": s.server_ip,
            "api_url": s.api_url
        } for s in servers]


async def get_server_by_id(server_id: int):
    async with async_session() as session:
        s = await session.get(ServersVPN, server_id)
        if not s:
            return None
        return {
            "idServerVPN": s.idServerVPN,
            "nameVPN": s.nameVPN,
            "price_usdt": str(s.price_usdt),
            "api_url": s.api_url
        }
        
        
async def get_servers_full():
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN).where(ServersVPN.is_active == True))
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        rate_val = rate.rate if rate else Decimal("1")

        result = []
        for s in servers:
            # Получаем тарифы
            tariffs_rows = await session.scalars(
                select(Tariff).where(Tariff.server_id == s.idServerVPN, Tariff.is_active == True)
            )
            tariffs_list = []
            for t in tariffs_rows:
                tariffs_list.append({
                    "idTarif": t.idTarif,
                    "days": t.days,
                    "price_usdt": str(t.price_tarif),
                    "price_stars": int(t.price_tarif / rate_val)
                })

            # Получаем тип VPN и страну
            type_vpn = await session.get(TypesVPN, s.idTypeVPN)
            country = await session.get(CountriesVPN, s.idCountry)

            result.append({
                "idServerVPN": s.idServerVPN,
                "nameVPN": s.nameVPN,
                "type_vpn": type_vpn.nameType if type_vpn else "",
                "country": country.nameCountry if country else "",
                "tariffs": tariffs_list
            })
        return result


# =======================
# --- СОЗДАНИЕ КЛЮЧА, УСПЕШНАЯ ОПЛАТА
# =======================
async def create_vpn_for_user(user_id: int, server_id: int, tariff_id: int):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        server = await session.get(ServersVPN, server_id)
        if not server:
            raise ValueError("Server not found")

        tariff = await session.get(Tariff, tariff_id)
        if not tariff or not tariff.is_active:
            raise ValueError("Tariff not found")

        # Создаём ключ через OutlineAPI
        outline = OutlineAPI(server.api_url, server.api_token)
        key_data = outline.create_key(name=f"VPN User {user_id}")

        now = datetime.utcnow()
        expires_at = now + timedelta(days=tariff.days)

        # Сохраняем VPNKey
        vpn_key = VPNKey(
            idUser=user_id,
            idServerVPN=server_id,
            provider="outline",
            provider_key_id=key_data["id"],
            access_data=key_data.get("accessUrl") or key_data.get("access_url") or key_data["id"],
            created_at=now,
            expires_at=expires_at,
            is_active=True
        )
        session.add(vpn_key)
        await session.flush()  # присвоит vpn_key.id

        # Создаём подписку
        vpn_sub = VPNSubscription(
            idUser=user_id,
            vpn_key_id=vpn_key.id,
            started_at=now,
            expires_at=expires_at,
            status="active"
        )
        session.add(vpn_sub)

        await session.commit()
        await session.refresh(vpn_key)
        await session.refresh(vpn_sub)

        return {
            "vpn_key_id": vpn_key.id,
            "access_data": vpn_key.access_data,
            "expires_at": vpn_key.expires_at.isoformat(),
            "subscription_id": vpn_sub.id
        }







# =======================
# --- USER: MY VPNs ---
# =======================
async def get_my_vpns(tg_id: int) -> List[dict]:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        rows = await session.execute(select(VPNSubscription, VPNKey, ServersVPN)
            .join(VPNKey, VPNSubscription.vpn_key_id == VPNKey.id)
            .join(ServersVPN, VPNKey.idServerVPN == ServersVPN.idServerVPN)
            .where(VPNSubscription.idUser == user.idUser)
        )

        result = []
        for sub, key, server in rows:
            result.append({
                "vpn_key_id": key.id,
                "server_id": server.idServerVPN,
                "serverName": server.nameVPN,
                "access_data": key.access_data,
                "expires_at": key.expires_at.isoformat(),
                "is_active": key.is_active,
                "status": sub.status
            })
        return result
    
    
# =======================
# --- ADMIN: USERS ---
# =======================

async def admin_get_users():
    async with async_session() as session:
        users = await session.scalars(select(User))
        return [{
            "idUser": u.idUser,
            "tg_id": u.tg_id,
            "userRole": u.userRole,
            "referrer_id": u.referrer_id
        } for u in users]


# =======================
# --- ADMIN: TYPES ---
# =======================

async def admin_get_types():
    async with async_session() as session:
        types = await session.scalars(select(TypesVPN))
        return [{
            "idTypeVPN": t.idTypeVPN,
            "nameType": t.nameType,
            "descriptionType": t.descriptionType
        } for t in types]


# =======================
# --- ADMIN: COUNTRIES ---
# =======================

async def admin_get_countries():
    async with async_session() as session:
        countries = await session.scalars(select(CountriesVPN))
        return [{
            "idCountry": c.idCountry,
            "nameCountry": c.nameCountry
        } for c in countries]


# =======================
# --- ADMIN: SERVERS ---
# =======================

async def admin_get_servers():
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN))
        result = []
        for s in servers:
            result.append({
                "idServerVPN": s.idServerVPN,
                "nameVPN": s.nameVPN,
                "price_usdt": str(s.price_usdt),
                "max_conn": s.max_conn,
                "now_conn": s.now_conn,
                "server_ip": s.server_ip,
                "api_url": s.api_url,
                "api_token": s.api_token,
                "is_active": s.is_active,
                "idTypeVPN": s.idTypeVPN,
                "idCountry": s.idCountry
            })
        return result


# =========================================================
# --- ADMIN: TYPES VPN (CRUD)
# =========================================================

async def admin_add_type(nameType: str, descriptionType: str):
    if not nameType or not descriptionType:
        raise ValueError("nameType и descriptionType обязательны")

    async with async_session() as session:
        t = TypesVPN(nameType=nameType, descriptionType=descriptionType)
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return {
            "idTypeVPN": t.idTypeVPN,
            "nameType": t.nameType,
            "descriptionType": t.descriptionType
        }


async def admin_update_type(type_id: int, nameType: str, descriptionType: str):
    async with async_session() as session:
        t = await session.get(TypesVPN, type_id)
        if not t:
            raise ValueError("TypeVPN не найден")

        await session.execute(update(TypesVPN).where(TypesVPN.idTypeVPN == type_id)
            .values(
                nameType=nameType,
                descriptionType=descriptionType
            )
        )
        await session.commit()
        return {"status": "ok"}


async def admin_delete_type(type_id: int):
    async with async_session() as session:
        t = await session.get(TypesVPN, type_id)
        if not t:
            raise ValueError("TypeVPN не найден")

        await session.delete(t)
        await session.commit()
        return {"status": "ok"}


# =========================================================
# --- ADMIN: COUNTRIES VPN (CRUD)
# =========================================================

async def admin_add_country(nameCountry: str):
    if not nameCountry:
        raise ValueError("nameCountry обязателен")

    async with async_session() as session:
        c = CountriesVPN(nameCountry=nameCountry)
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {
            "idCountry": c.idCountry,
            "nameCountry": c.nameCountry
        }


async def admin_update_country(country_id: int, nameCountry: str):
    async with async_session() as session:
        c = await session.get(CountriesVPN, country_id)
        if not c:
            raise ValueError("CountryVPN не найден")

        await session.execute(update(CountriesVPN).where(CountriesVPN.idCountry == country_id)
            .values(nameCountry=nameCountry)
        )
        await session.commit()
        return {"status": "ok"}


async def admin_delete_country(country_id: int):
    async with async_session() as session:
        c = await session.get(CountriesVPN, country_id)
        if not c:
            raise ValueError("CountryVPN не найден")

        await session.delete(c)
        await session.commit()
        return {"status": "ok"}


# =========================================================
# --- ADMIN: EXCHANGE RATES (CRUD)
# =========================================================

from models import ExchangeRate

async def admin_get_exchange_rates():
    async with async_session() as session:
        rates = await session.scalars(select(ExchangeRate))
        return [{
            "id": r.id,
            "currency": r.currency,
            "rate_to_usdt": str(r.rate_to_usdt),
            "updated_at": r.updated_at.isoformat()
        } for r in rates]


async def admin_add_exchange_rate(currency: str, rate_to_usdt: Decimal):
    async with async_session() as session:
        rate = ExchangeRate(currency=currency,rate_to_usdt=rate_to_usdt)
        
        session.add(rate)
        await session.commit()
        await session.refresh(rate)
        return {
            "id": rate.id,
            "currency": rate.currency,
            "rate_to_usdt": str(rate.rate_to_usdt)
        }


async def admin_update_exchange_rate(rate_id: int, rate_to_usdt: Decimal):
    async with async_session() as session:
        rate = await session.get(ExchangeRate, rate_id)
        if not rate:
            raise ValueError("ExchangeRate не найден")

        await session.execute(update(ExchangeRate).where(ExchangeRate.id == rate_id)
            .values(
                rate_to_usdt=rate_to_usdt,
                updated_at=datetime.utcnow()
            )
        )
        await session.commit()
        return {"status": "ok"}


async def admin_delete_exchange_rate(rate_id: int):
    async with async_session() as session:
        rate = await session.get(ExchangeRate, rate_id)
        if not rate:
            raise ValueError("ExchangeRate не найден")

        await session.delete(rate)
        await session.commit()
        return {"status": "ok"}


# =========================================================
# --- ADMIN: SERVERS VPN (CRUD)
# =========================================================

async def admin_add_server(data):
    async with async_session() as session:
        server = ServersVPN(
            nameVPN=data.nameVPN,
            price_usdt=data.price_usdt,
            max_conn=data.max_conn,
            server_ip=data.server_ip,
            api_url=data.api_url,
            api_token=data.api_token,
            idTypeVPN=data.idTypeVPN,
            idCountry=data.idCountry,
            is_active=data.is_active
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        return {
            "idServerVPN": server.idServerVPN,
            "nameVPN": server.nameVPN
        }


async def admin_update_server(server_id: int, data):
    async with async_session() as session:
        server = await session.get(ServersVPN, server_id)
        if not server:
            raise ValueError("ServerVPN не найден")

        await session.execute(update(ServersVPN).where(ServersVPN.idServerVPN == server_id)
            .values(
                nameVPN=data.nameVPN,
                price_usdt=data.price_usdt,
                max_conn=data.max_conn,
                server_ip=data.server_ip,
                api_url=data.api_url,
                api_token=data.api_token,
                idTypeVPN=data.idTypeVPN,
                idCountry=data.idCountry,
                is_active=data.is_active
            )
        )
        await session.commit()
        return {"status": "ok"}


async def admin_delete_server(server_id: int):
    async with async_session() as session:
        server = await session.get(ServersVPN, server_id)
        if not server:
            raise ValueError("ServerVPN не найден")

        await session.delete(server)
        await session.commit()
        return {"status": "ok"}
    
# =======================
# --- GET TARIFFS ---
# =======================
async def get_server_tariffs(server_id: int):
    async with async_session() as session:
        tariffs = await session.scalars(
            select(Tariff).where(Tariff.server_id == server_id, Tariff.is_active == True)
        )
        return [{
            "idTarif": t.idTarif,
            "days": t.days,
            "price_usdt": str(t.price_tarif)
        } for t in tariffs]
        

# --- СОЗДАНИЕ ЗАКАЗА ---    
async def create_order(user_id: int, server_id: int, tariff_id: int, amount_usdt: Decimal, currency: str = "XTR"):
    async with async_session() as session:
        order = Order(
            idUser=user_id,
            server_id=server_id,
            idTarif=tariff_id,
            amount=int(amount_usdt),
            currency=currency,
            status="pending"
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return {
            "order_id": order.id,
            "amount": str(amount_usdt),
            "currency": currency,
            "idTarif": tariff_id
        }
        
        
# --- ОПЛАТА И ПРОДЛЕНИЕ --- 
async def pay_and_extend_vpn(user_id: int, server_id: int, tariff_id: int):
    async with async_session() as session:
        tariff = await session.get(Tariff, tariff_id)
        if not tariff or not tariff.is_active:
            raise ValueError("Тариф не найден")

        vpn_key = await session.scalar(
            select(VPNKey)
            .where(VPNKey.idUser == user_id, VPNKey.idServerVPN == server_id)
        )

        now = datetime.utcnow()
        new_expiry = now + timedelta(days=tariff.days)

        if vpn_key:
            if vpn_key.expires_at > now:
                vpn_key.expires_at += timedelta(days=tariff.days)
            else:
                vpn_key.expires_at = new_expiry
        else:
            server = await session.get(ServersVPN, server_id)
            outline = OutlineAPI(server.api_url, server.api_token)
            key_data = outline.create_key(name=f"VPN User {user_id}")
            vpn_key = VPNKey(
                idUser=user_id,
                idServerVPN=server_id,
                provider="outline",
                provider_key_id=key_data["id"],
                access_data=key_data["accessUrl"],
                expires_at=new_expiry,
                is_active=True
            )
            session.add(vpn_key)

        await session.commit()
        await session.refresh(vpn_key)
        return {
            "vpn_key_id": vpn_key.id,
            "server_id": vpn_key.idServerVPN,
            "access_data": vpn_key.access_data,
            "expires_at": vpn_key.expires_at.isoformat()
        }