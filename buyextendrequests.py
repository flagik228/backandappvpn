from models import (async_session, User, UserWallet, WalletTransaction, VPNSubscription, TypesVPN,
    CountriesVPN, ServersVPN, Tariff, ExchangeRate, Order, Payment, ReferralConfig, ReferralEarning)
from typing import List
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from urllib.parse import quote
from xui_api import XUIApi
from sqlalchemy import select
import requestsfile as rq


# --- СОЗДАНИЕ ЗАКАЗА ---    
async def create_order(user_id: int,server_id: int,tariff_id: int,amount_usdt: Decimal,purpose_order: str = "buy",currency: str = "XTR"):
    async with async_session() as session:
        order = Order(
            idUser=user_id,
            server_id=server_id,
            idTarif=tariff_id,
            purpose_order=purpose_order,
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


# =====================================================================
# --- СОЗДАНИЕ КЛЮЧА, УСПЕШНАЯ ОПЛАТА
# =====================================================================
async def create_vpn_xui(user_id: int, server_id: int, tariff_days: int):
    async with async_session() as session:
        user = await session.get(User, user_id)
        server = await session.get(ServersVPN, server_id)

        if not user or not server:
            raise Exception("User or server not found")

        # создаём XUI API
        xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
        client_email = await rq.generate_unique_client_email(session, user_id, server, xui)

        inbound = await xui.get_inbound_by_port(server.inbound_port)
        if not inbound:
            raise Exception("Inbound not found")

        client = await xui.add_client(inbound_id=inbound.id,email=client_email,days=tariff_days)
        uuid = client["uuid"]

        # 🔍 получаем Reality настройки
        stream = inbound.stream_settings
        reality = stream.reality_settings

        public_key = reality["settings"]["publicKey"]
        server_name = reality["serverNames"][0]
        short_id = reality["shortIds"][0]

        query = {
            "type": stream.network,
            "security": stream.security,
            "pbk": public_key,
            "fp": "chrome",
            "sni": server_name,
            "sid": short_id,
        }

        query_str = "&".join(f"{k}={quote(str(v))}" for k, v in query.items())

        access_link = (
            f"vless://{uuid}@{server.server_ip}:{server.inbound_port}"
            f"?{query_str}#{client_email}"
        )

        now = datetime.utcnow()
        expires_at = now + timedelta(days=tariff_days)

        subscription = VPNSubscription(
            idUser=user_id,
            idServerVPN=server_id,
            provider="xui",
            provider_client_email=client_email,
            provider_client_uuid=uuid,
            access_data=access_link,
            created_at=now,
            expires_at=expires_at,
            is_active=True,
            status="active"
        )

        session.add(subscription)
        await rq.recalc_server_load(session, server_id)
        await session.commit()

        return {
            "subscription_id": subscription.id,
            "access_data": access_link,
            "expires_at": expires_at.isoformat(), # для API / логики
            "expires_at_human": rq.format_datetime_ru(expires_at) # для человека
        }
        
        
# --- ОПЛАТА И ПРОДЛЕНИЕ --- 
async def pay_and_extend_vpn(subscription_id: int, tariff_id: int):
    async with async_session() as session:
        tariff = await session.get(Tariff, tariff_id)
        if not tariff:
            raise ValueError("Tariff not found")

        sub = await session.get(VPNSubscription, subscription_id)

        if not sub:
            raise ValueError("Subscription not found")

        server = await session.get(ServersVPN, sub.idServerVPN)
        xui = XUIApi(server.api_url, server.xui_username, server.xui_password)

        inbound = await xui.get_inbound_by_port(server.inbound_port)
        if not inbound:
            raise Exception("Inbound not found")

        await xui.extend_client(inbound_id=inbound.id,client_email=sub.provider_client_email,days=tariff.days)

        now = datetime.now(timezone.utc)

        if sub.expires_at and sub.expires_at > now:
            sub.expires_at += timedelta(days=tariff.days)
        else:
            sub.expires_at = now + timedelta(days=tariff.days)

        sub.is_active = True
        sub.status = "active"
        await rq.recalc_server_load(session, sub.idServerVPN)
        await session.commit()

        return {
            "subscription_id": sub.id,
            "access_data": sub.access_data,
            "days_added": tariff.days,
            "expires_at": sub.expires_at.isoformat(),
            "expires_at_human": rq.format_datetime_ru(sub.expires_at)
        }


# =======================
# --- УДАЛЕНИЕ КЛЮЧА
async def remove_vpn_xui(subscription: VPNSubscription):
    async with async_session() as session:
        server = await session.get(ServersVPN, subscription.idServerVPN)
        if not server:
            raise Exception("Сервер не найден")

        xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
        inbound = await xui.get_inbound_by_port(server.inbound_port)
        if not inbound:
            raise Exception("Inbound не найден")

        inbound_id = inbound.id

        try:
            await xui.remove_client(inbound_id=inbound_id,email=subscription.provider_client_email)
        except Exception as e:
            raise Exception(f"Не удалось удалить клиента на XUI: {e}")

        # Деактивируем ключ в БД
        subscription.is_active = False
        subscription.status = "expired"
        await session.commit()


# =====================================================================
# --- ПОКУПКА VPN С БАЛАНСА ---
# =====================================================================
async def buy_vpn_from_balance(tg_id: int, tariff_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("User not found")

        tariff = await session.get(Tariff, tariff_id)
        if not tariff or not tariff.is_active:
            raise Exception("Tariff not found")

        server = await session.get(ServersVPN, tariff.server_id)
        if not server:
            raise Exception("Server not found")

        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.idUser))
        if not wallet:
            raise Exception("Wallet not found")

        price = Decimal(tariff.price_tarif)

        # ❌ НЕ ХВАТАЕТ ДЕНЕГ
        if wallet.balance_usdt < price:
            raise Exception("NOT_ENOUGH_BALANCE")

        # 1️⃣ Списываем баланс
        wallet.balance_usdt -= price

        tx = WalletTransaction(
            wallet_id=wallet.id,
            amount=-price,
            type="withdrawal",
            description=f"VPN purchase ({tariff.days} days)"
        )
        session.add(tx)

        # 2️⃣ Создаём заказ
        order = Order(
            idUser=user.idUser,
            server_id=server.idServerVPN,
            idTarif=tariff.idTarif,
            purpose_order="buy",
            amount=price,
            currency="USDT",
            status="processing"
        )
        session.add(order)
        await session.flush()

        # 3️⃣ Платёж (внутренний)
        payment = Payment(
            order_id=order.id,
            provider="balance",
            provider_payment_id=f"balance_{order.id}",
            status="paid"
        )
        session.add(payment)

        # 4️⃣ Выдаём VPN
        vpn_data = await create_vpn_xui(
            user_id=user.idUser,
            server_id=server.idServerVPN,
            tariff_days=tariff.days
        )

        order.status = "completed"

        # 5️⃣ Рефералка
        await rq.process_referral_reward(session, order)

        await session.commit()

        return {
            "order_id": order.id,
            "access_data": vpn_data["access_data"],
            "expires_at_human": vpn_data["expires_at_human"],
            "server_name": server.nameVPN
        }
