from sqlalchemy import select, update, delete
from models import (async_session, User, UserWallet, WalletTransaction, VPNSubscription, TypesVPN,
    CountriesVPN, ServersVPN, Tariff, ExchangeRate, Order, Payment, ReferralConfig, ReferralEarning)
from typing import List
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import func
from urllib.parse import quote

from xui_api import XUIApi

# --- ADMIN ------------------------------------------------------------

# =======================
# --- ADMIN: USERS ---
# =======================
async def admin_get_users():
    async with async_session() as session:
        users = await session.scalars(select(User))
        return [{
            "idUser": u.idUser,
            "tg_id": u.tg_id,
            "tg_username": u.tg_username,
            "userRole": u.userRole,
            "referrer_id": u.referrer_id,
            "created_at": u.created_at.isoformat()
        } for u in users]
        
async def admin_add_user(tg_id: int,tg_username: str | None,userRole: str,referrer_id: int | None):
    async with async_session() as session:
        user = User(
            tg_id=tg_id,
            tg_username=tg_username,
            userRole=userRole,
            referrer_id=referrer_id
        )
        session.add(user)
        await session.flush()

        session.add(UserWallet(idUser=user.idUser))
        await session.commit()
        return {"idUser": user.idUser}

async def admin_update_user(user_id: int, data: dict):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        for field in ["tg_id", "tg_username", "userRole", "referrer_id"]:
            if field in data:
                setattr(user, field, data[field])

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
# --- ADMIN: UserWallet ---
# =======================
async def admin_get_wallets():
    async with async_session() as session:
        wallets = (await session.scalars(select(UserWallet))).all()
        return [{
            "id": w.id,
            "idUser": w.idUser,
            "balance_usdt": str(w.balance_usdt),
            "updated_at": w.updated_at.isoformat()
        } for w in wallets]

async def admin_add_wallet(data: dict):
    async with async_session() as session:
        wallet = UserWallet(**data)
        session.add(wallet)
        await session.commit()
        await session.refresh(wallet)
        return {"id": wallet.id}

async def admin_update_wallet(wallet_id: int, data: dict):
    async with async_session() as session:
        wallet = await session.get(UserWallet, wallet_id)
        if not wallet:
            raise ValueError("Wallet not found")

        for k, v in data.items():
            setattr(wallet, k, v)

        wallet.updated_at = datetime.utcnow()
        await session.commit()
        return {"status": "ok"}

async def admin_delete_wallet(wallet_id: int):
    async with async_session() as session:
        wallet = await session.get(UserWallet, wallet_id)
        if not wallet:
            raise ValueError("Wallet not found")

        await session.delete(wallet)
        await session.commit()
        return {"status": "ok"}
    
    

# =======================
# --- ADMIN: WalletTransaction ---
# =======================
async def admin_get_wallet_transactions():
    async with async_session() as session:
        txs = (await session.scalars(select(WalletTransaction))).all()
        return [{
            "id": t.id,
            "wallet_id": t.wallet_id,
            "amount": str(t.amount),
            "type": t.type,
            "description": t.description,
            "created_at": t.created_at.isoformat()
        } for t in txs]

async def admin_add_wallet_transaction(data: dict):
    async with async_session() as session:
        tx = WalletTransaction(**data)
        session.add(tx)
        await session.commit()
        await session.refresh(tx)
        return {"id": tx.id}

async def admin_update_wallet_transaction(tx_id: int, data: dict):
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx:
            raise ValueError("Transaction not found")

        for k, v in data.items():
            setattr(tx, k, v)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_wallet_transaction(tx_id: int):
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx:
            raise ValueError("Transaction not found")

        await session.delete(tx)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: TYPES VPN (CRUD)
# =========================================================
async def admin_get_types():
    async with async_session() as session:
        types = await session.scalars(select(TypesVPN))
        return [{
            "idTypeVPN": t.idTypeVPN,
            "nameType": t.nameType,
            "descriptionType": t.descriptionType
        } for t in types]

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
async def admin_get_countries():
    async with async_session() as session:
        countries = await session.scalars(select(CountriesVPN))
        return [{
            "idCountry": c.idCountry,
            "nameCountry": c.nameCountry
        } for c in countries]

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
# --- ADMIN: SERVERS VPN
# =========================================================
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
                "xui_username": s.xui_username,
                "xui_password": s.xui_password,
                "inbound_port": s.inbound_port,
                "is_active": s.is_active,
                "idTypeVPN": s.idTypeVPN,
                "idCountry": s.idCountry
            })
        return result

async def admin_add_server(data):
    async with async_session() as session:
        server = ServersVPN(
            nameVPN=data.nameVPN,
            price_usdt=data.price_usdt,
            max_conn=data.max_conn,
            now_conn=0,  # при создании новый сервер всегда 0
            server_ip=data.server_ip,
            api_url=data.api_url,
            api_token=data.api_token,
            xui_username=data.xui_username,
            xui_password=data.xui_password,
            inbound_port=data.inbound_port,
            is_active=data.is_active,
            idTypeVPN=data.idTypeVPN,
            idCountry=data.idCountry
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

        await session.execute(
            update(ServersVPN)
            .where(ServersVPN.idServerVPN == server_id)
            .values(
                nameVPN=data["nameVPN"],
                price_usdt=data["price_usdt"],
                max_conn=data["max_conn"],
                server_ip=data["server_ip"],
                api_url=data["api_url"],
                api_token=data["api_token"],
                xui_username=data["xui_username"],
                xui_password=data["xui_password"],
                inbound_port=data["inbound_port"],
                is_active=data["is_active"],
                idTypeVPN=data["idTypeVPN"],
                idCountry=data["idCountry"]
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



# =========================================================
# --- ADMIN: Tariff
# =========================================================
async def admin_get_tariffs(server_id: int):
    async with async_session() as session:
        tariffs = await session.scalars(select(Tariff).where(Tariff.server_id == server_id))
        return [{
            "idTarif": t.idTarif,
            "server_id": t.server_id,
            "days": t.days,
            "price_tarif": str(t.price_tarif),
            "is_active": t.is_active
        } for t in tariffs]

async def admin_add_tariff(server_id: int, days: int, price_tarif: Decimal, is_active: bool):
    async with async_session() as session:
        t = Tariff(
            server_id=server_id,
            days=days,
            price_tarif=price_tarif,
            is_active=is_active
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return {"idTarif": t.idTarif}

async def admin_update_tariff(tariff_id: int, days: int, price_tarif: Decimal, is_active: bool):
    async with async_session() as session:
        t = await session.get(Tariff, tariff_id)
        if not t:
            raise ValueError("Tariff not found")

        t.days = days
        t.price_tarif = price_tarif
        t.is_active = is_active
        await session.commit()
        return {"status": "ok"}

async def admin_delete_tariff(tariff_id: int):
    async with async_session() as session:
        t = await session.get(Tariff, tariff_id)
        if not t:
            raise ValueError("Tariff not found")

        await session.delete(t)
        await session.commit()
        return {"status": "ok"}
    


# =========================================================
# --- ADMIN: EXCHANGE RATES (CRUD)
# =========================================================
async def admin_get_exchange_rate(pair: str):
    async with async_session() as session:
        rate = await session.scalar(
            select(ExchangeRate).where(ExchangeRate.pair == pair)
        )
        if not rate:
            return None

        return {
            "pair": rate.pair,
            "rate": str(rate.rate),
            "updated_at": rate.updated_at.isoformat()
        }

async def admin_set_exchange_rate(pair: str, rate_value: Decimal):
    async with async_session() as session:
        rate = await session.scalar(
            select(ExchangeRate).where(ExchangeRate.pair == pair)
        )

        if rate:
            rate.rate = rate_value
            rate.updated_at = datetime.utcnow()
        else:
            rate = ExchangeRate(
                pair=pair,
                rate=rate_value
            )
            session.add(rate)

        await session.commit()

        return {
            "pair": rate.pair,
            "rate": str(rate.rate),
            "updated_at": rate.updated_at.isoformat()
        }
    

    
# =========================================================
# --- ADMIN: Order
# =========================================================
ALLOWED_PURPOSES = {"buy", "extension"}

async def admin_get_orders():
    async with async_session() as session:
        orders = (await session.scalars(select(Order))).all()
        return [{
            "id": o.id,
            "idUser": o.idUser,
            "server_id": o.server_id,
            "idTarif": o.idTarif,
            "purpose_order": o.purpose_order,
            "amount": o.amount,
            "currency": o.currency,
            "status": o.status,
            "created_at": o.created_at.isoformat()
        } for o in orders]

async def admin_add_order(data):
    if data.get("purpose_order") not in ALLOWED_PURPOSES:
        raise ValueError("Invalid purpose_order")
    
    async with async_session() as session:
        order = Order(**data)
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return {"id": order.id}

async def admin_update_order(order_id: int, data: dict):
    if "purpose_order" in data:
        if data["purpose_order"] not in ALLOWED_PURPOSES:
            raise ValueError("Invalid purpose_order")
        
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            raise ValueError("Order not found")

        for key, value in data.items():
            setattr(order, key, value)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_order(order_id: int):
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            raise ValueError("Order not found")

        await session.delete(order)
        await session.commit()
        return {"status": "ok"}

async def admin_get_all_tariffs():
    async with async_session() as session:
        tariffs = (await session.scalars(select(Tariff))).all()
        return [
            {
                "idTarif": t.idTarif,
                "server_id": t.server_id,
                "days": t.days,
                "price_tarif": str(t.price_tarif),
                "is_active": t.is_active
            }
            for t in tariffs
        ]



# =========================================================
# --- ADMIN: Payment
# =========================================================
async def admin_get_payments():
    async with async_session() as session:
        payments = (await session.scalars(select(Payment))).all()
        return [{
            "id": p.id,
            "order_id": p.order_id,
            "provider": p.provider,
            "provider_payment_id": p.provider_payment_id,
            "status": p.status,
            "created_at": p.created_at.isoformat()
        } for p in payments]

async def admin_add_payment(data: dict):
    async with async_session() as session:
        payment = Payment(**data)
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return {"id": payment.id}

async def admin_update_payment(payment_id: int, data: dict):
    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            raise ValueError("Payment not found")

        for key, value in data.items():
            setattr(payment, key, value)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_payment(payment_id: int):
    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            raise ValueError("Payment not found")

        await session.delete(payment)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: VPNKey
"""
async def admin_get_vpn_keys():
    async with async_session() as session:
        keys = (await session.scalars(select(VPNKey))).all()
        return [{
            "id": k.id,
            "idUser": k.idUser,
            "idServerVPN": k.idServerVPN,
            "provider": k.provider,
            "provider_client_email": k.provider_client_email,
            "provider_client_uuid": k.provider_client_uuid,
            "access_data": k.access_data,
            "created_at": k.created_at.isoformat(),
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "is_active": k.is_active
        } for k in keys]

async def admin_add_vpn_key(data: dict):
    async with async_session() as session:
        key = VPNKey(**data)
        session.add(key)
        await session.commit()
        await session.refresh(key)
        return {"id": key.id}

async def admin_update_vpn_key(key_id: int, data: dict):
    async with async_session() as session:
        key = await session.get(VPNKey, key_id)
        if not key:
            raise ValueError("VPNKey not found")

        for k, v in data.items():
            setattr(key, k, v)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_vpn_key(key_id: int):
    async with async_session() as session:
        key = await session.get(VPNKey, key_id)
        if not key:
            raise ValueError("VPNKey not found")

        await session.delete(key)
        await session.commit()
        return {"status": "ok"}
"""


# =========================================================
# --- ADMIN: VPNSubscription
# =========================================================
async def admin_get_vpn_subscriptions():
    async with async_session() as session:
        subs = (await session.scalars(select(VPNSubscription))).all()
        return [{
            "id": s.id,
            "idUser": s.idUser,
            "idServerVPN": s.idServerVPN,
            "provider": s.provider,
            "provider_client_email": s.provider_client_email,
            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
            "is_active": s.is_active,
            "status": s.status
        } for s in subs]


async def admin_add_vpn_subscription(data: dict):
    async with async_session() as session:
        sub = VPNSubscription(
            **data,
            created_at=data.get("created_at") or datetime.utcnow()
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return {"id": sub.id}


async def admin_update_vpn_subscription(sub_id: int, data: dict):
    async with async_session() as session:
        sub = await session.get(VPNSubscription, sub_id)
        if not sub:
            raise ValueError("Subscription not found")

        for k, v in data.items():
            setattr(sub, k, v)

        await session.commit()
        return {"status": "ok"}


async def admin_delete_vpn_subscription(sub_id: int):
    async with async_session() as session:
        sub = await session.get(VPNSubscription, sub_id)
        if not sub:
            raise ValueError("Subscription not found")

        await session.delete(sub)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: ReferralConfig
# =========================================================
async def admin_get_referral_config():
    async with async_session() as session:
        rows = await session.scalars(select(ReferralConfig))
        return [{
            "id": c.id,
            "percent": c.percent,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat()
        } for c in rows]

async def admin_add_referral_config(percent: int, is_active: bool):
    async with async_session() as session:

        if is_active:
            await session.execute(
                update(ReferralConfig).values(is_active=False)
            )

        c = ReferralConfig(percent=percent, is_active=is_active)
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {"id": c.id}

async def admin_update_referral_config(config_id: int, percent: int, is_active: bool):
    async with async_session() as session:
        c = await session.get(ReferralConfig, config_id)
        if not c:
            raise ValueError("ReferralConfig not found")

        if is_active:
            await session.execute(
                update(ReferralConfig)
                .where(ReferralConfig.id != config_id)
                .values(is_active=False)
            )

        c.percent = percent
        c.is_active = is_active
        await session.commit()
        return {"status": "ok"}

async def admin_delete_referral_config(config_id: int):
    async with async_session() as session:
        c = await session.get(ReferralConfig, config_id)
        if not c:
            raise ValueError("Config not found")

        await session.delete(c)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: ReferralEarning
# =========================================================
async def admin_get_referral_earnings():
    async with async_session() as session:
        earnings = (await session.scalars(select(ReferralEarning))).all()
        return [{
            "id": e.id,
            "referrer_id": e.referrer_id,
            "order_id": e.order_id,
            "percent": e.percent,
            "amount_usdt": str(e.amount_usdt),
            "created_at": e.created_at.isoformat()
        } for e in earnings]

async def admin_add_referral_earning(data: dict):
    async with async_session() as session:
        e = ReferralEarning(**data)
        session.add(e)
        await session.commit()
        await session.refresh(e)
        return {"id": e.id}

async def admin_update_referral_earning(earning_id: int, data: dict):
    async with async_session() as session:
        e = await session.get(ReferralEarning, earning_id)
        if not e:
            raise ValueError("ReferralEarning not found")

        for k, v in data.items():
            setattr(e, k, v)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_referral_earning(earning_id: int):
    async with async_session() as session:
        e = await session.get(ReferralEarning, earning_id)
        if not e:
            raise ValueError("ReferralEarning not found")

        await session.delete(e)
        await session.commit()
        return {"status": "ok"}