from models import (async_session, User, UserWallet, WalletTransaction, VPNSubscription, TypesVPN,
    CountriesVPN, ServersVPN, Tariff, ExchangeRate, Order, Payment, ReferralConfig, ReferralEarning,
    BundlePlan, BundleServer, BundleSubscription, BundleSubscriptionItem, BundleTariff)
from typing import List
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from xui_api import XUIApi
import uuid as uuid_lib
from sqlalchemy import select
import requestsfile as rq
import main as main


# СОЗДАНИЕ ЗАКАЗА    
async def create_order(user_id: int,server_id: int,tariff_id: int,amount_usdt: Decimal,purpose_order: str = "buy",currency: str = "XTR"):
    async with async_session() as session:
        order = Order(idUser=user_id,server_id=server_id,idTarif=tariff_id,purpose_order=purpose_order,
            amount=Decimal(amount_usdt),currency=currency,status="pending")
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return {"order_id": order.id,"amount": str(amount_usdt),"currency": currency,"idTarif": tariff_id}


# СОЗДАНИЕ КЛЮЧА покупка
async def create_vpn_xui(user_id: int, server_id: int, tariff_days: int):
    async with async_session() as session:
        user = await session.get(User, user_id)
        server = await session.get(ServersVPN, server_id)

        if not user or not server:
            raise Exception("User or server not found")

        xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
        client_email = await rq.generate_unique_client_email(session, user_id, server, xui)
        inbound = await xui.get_inbound_by_port(server.inbound_port)
        if not inbound:
            raise Exception("Inbound not found")

        sub_id = uuid_lib.uuid4().hex[:16]
        client = await xui.add_client(inbound_id=inbound.id,email=client_email,days=tariff_days,sub_id=sub_id)
        client_uuid = client["uuid"]
        sub_id = client.get("sub_id") or sub_id

        # получаем Reality настройки
        now = datetime.utcnow()
        expires_at = now + timedelta(days=tariff_days)

        access_token = uuid_lib.uuid4().hex
        subscription_url = rq.build_single_subscription_url(access_token)
        if not subscription_url:
            raise Exception("SUBSCRIPTION_URL_UNAVAILABLE")

        subscription = VPNSubscription(idUser=user_id,idServerVPN=server_id,provider="xui",provider_client_email=client_email,
            provider_client_uuid=client_uuid,subscription_id=sub_id,subscription_url=subscription_url,
            access_token=access_token,
            created_at=now,expires_at=expires_at,is_active=True,status="active")

        session.add(subscription)
        await rq.recalc_server_load(session, server_id)
        await session.commit()

        return {"subscription_id": subscription.id,"subscription_url": subscription_url,
            "expires_at": expires_at.isoformat(),"expires_at_human": rq.format_datetime_ru(expires_at)}
        
        
# продление
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

        extend_result = await xui.extend_client(
            inbound_id=inbound.id,
            client_email=sub.provider_client_email,
            days=tariff.days,
            sub_id=sub.subscription_id
        )
        if not sub.subscription_id and extend_result.get("sub_id"):
            sub.subscription_id = extend_result["sub_id"]
            sub.subscription_url = rq.build_single_subscription_url(sub.access_token)

        now = datetime.now(timezone.utc)

        if sub.expires_at and sub.expires_at > now:
            sub.expires_at += timedelta(days=tariff.days)
        else:
            sub.expires_at = now + timedelta(days=tariff.days)

        sub.is_active = True
        sub.status = "active"
        await rq.recalc_server_load(session, sub.idServerVPN)
        await session.commit()

        return {"subscription_id": sub.id,"days_added": tariff.days,
            "subscription_url": sub.subscription_url,
            "expires_at": sub.expires_at.isoformat(),"expires_at_human": rq.format_datetime_ru(sub.expires_at)}


# УДАЛЕНИЕ КЛЮЧА
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

        subscription.is_active = False
        subscription.status = "expired"
        await session.commit()


# ПОКУПКА VPN С БАЛАНСА
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
        if wallet.balance_usdt < price:
            raise Exception("NOT_ENOUGH_BALANCE")

        wallet.balance_usdt -= price
        tx = WalletTransaction(wallet_id=wallet.id,amount=-price,type="buy",description=f"VPN purchase ({tariff.days} days)")
        session.add(tx)

        order = Order(idUser=user.idUser,server_id=server.idServerVPN,idTarif=tariff.idTarif,purpose_order="buy",
            amount=price,currency="USDT",provider="balance",status="processing")
        session.add(order)
        await session.flush()

        payment = Payment(order_id=order.id,provider="balance",provider_payment_id=f"balance_{order.id}",status="paid")
        session.add(payment)

        vpn_data = await create_vpn_xui(user_id=user.idUser,server_id=server.idServerVPN,tariff_days=tariff.days)
        order.status = "completed"

        await rq.process_referral_reward(session, order)
        await session.commit()

        return {"order_id": order.id,
            "subscription_url": vpn_data.get("subscription_url"),
            "expires_at_human": vpn_data["expires_at_human"],"server_name": server.nameVPN}


# ПРОДЛЕНИЕ VPN С БАЛАНСА
async def extend_vpn_from_balance(tg_id: int, subscription_id: int, tariff_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("User not found")

        sub = await session.get(VPNSubscription, subscription_id)
        if not sub or sub.idUser != user.idUser:
            raise Exception("Subscription not found")

        tariff = await session.get(Tariff, tariff_id)
        if not tariff or not tariff.is_active:
            raise Exception("Tariff not found")
        if tariff.server_id != sub.idServerVPN:
            raise Exception("TARIFF_NOT_ALLOWED_FOR_THIS_VPN")


        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.idUser))
        if not wallet:
            raise Exception("Wallet not found")

        price = Decimal(tariff.price_tarif)
        if wallet.balance_usdt < price:
            raise Exception("NOT_ENOUGH_BALANCE")

        wallet.balance_usdt -= price

        session.add(WalletTransaction(wallet_id=wallet.id,amount=-price,type="extend",description=f"VPN extend ({tariff.days} days)"))

        order = Order(
            idUser=user.idUser,
            server_id=sub.idServerVPN,
            idTarif=tariff.idTarif,
            subscription_id=sub.id,
            purpose_order="extension",
            amount=price,
            currency="USDT",
            provider="balance",
            status="processing"
        )
        session.add(order)
        await session.flush()

        session.add(Payment(order_id=order.id,provider="balance",provider_payment_id=f"balance_{order.id}",status="paid"))

        vpn_data = await pay_and_extend_vpn(subscription_id=sub.id,tariff_id=tariff.idTarif)

        order.status = "completed"

        await rq.process_referral_reward(session, order)
        await session.commit()

        return vpn_data


# BUNDLE: BUY/RENEW (ALL SERVERS)
async def buy_bundle_from_balance(tg_id: int, bundle_tariff_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("User not found")

        tariff = await session.get(BundleTariff, bundle_tariff_id)
        if not tariff or not tariff.is_active:
            raise Exception("Tariff not found")

        plan = await session.get(BundlePlan, tariff.bundle_plan_id)
        if not plan or not plan.is_active:
            raise Exception("Plan not found")

        server_ids = (await session.scalars(
            select(BundleServer.server_id).where(BundleServer.bundle_plan_id == plan.id)
        )).all()
        if not server_ids:
            raise Exception("PLAN_HAS_NO_SERVERS")

        servers = (await session.scalars(
            select(ServersVPN).where(ServersVPN.idServerVPN.in_(server_ids), ServersVPN.is_active == True)
        )).all()
        if not servers:
            raise Exception("NO_ACTIVE_SERVERS")

        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.idUser))
        if not wallet:
            raise Exception("Wallet not found")

        price = Decimal(tariff.price_usdt)
        if wallet.balance_usdt < price:
            raise Exception("NOT_ENOUGH_BALANCE")

        wallet.balance_usdt -= price
        session.add(WalletTransaction(
            wallet_id=wallet.id,
            amount=-price,
            type="buy",
            description=f"Bundle plan purchase ({tariff.days} days)"
        ))

        order = Order(
            idUser=user.idUser,
            server_id=servers[0].idServerVPN,
            idTarif=None,
            subscription_id=None,
            bundle_plan_id=plan.id,
            bundle_tariff_id=tariff.id,
            purpose_order="bundle_buy",
            amount=price,
            currency="USDT",
            provider="balance",
            status="processing"
        )
        session.add(order)
        await session.flush()

        payment = Payment(order_id=order.id,provider="balance",provider_payment_id=f"balance_{order.id}",status="paid")
        session.add(payment)

        bundle_sub = await create_bundle_subscription(session, user.idUser, plan, servers, tariff.days)
        order.bundle_subscription_id = bundle_sub.id

        order.status = "completed"
        await session.commit()

        return {
            "order_id": order.id,
            "subscription_url": bundle_sub.subscription_url,
            "expires_at_human": rq.format_datetime_ru(bundle_sub.expires_at),
            "plan_name": plan.name
        }


async def renew_bundle_from_balance(tg_id: int, bundle_subscription_id: int, bundle_tariff_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("User not found")

        bundle_sub = await session.get(BundleSubscription, bundle_subscription_id)
        if not bundle_sub or bundle_sub.idUser != user.idUser:
            raise Exception("Subscription not found")

        tariff = await session.get(BundleTariff, bundle_tariff_id)
        if not tariff or not tariff.is_active:
            raise Exception("Tariff not found")

        plan = await session.get(BundlePlan, tariff.bundle_plan_id)
        if not plan or not plan.is_active:
            raise Exception("Plan not found")

        if plan.id != bundle_sub.bundle_plan_id:
            raise Exception("TARIFF_NOT_ALLOWED_FOR_THIS_BUNDLE")

        server_ids = (await session.scalars(
            select(BundleServer.server_id).where(BundleServer.bundle_plan_id == plan.id)
        )).all()
        if not server_ids:
            raise Exception("PLAN_HAS_NO_SERVERS")

        servers = (await session.scalars(
            select(ServersVPN).where(ServersVPN.idServerVPN.in_(server_ids), ServersVPN.is_active == True)
        )).all()
        if not servers:
            raise Exception("NO_ACTIVE_SERVERS")

        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.idUser))
        if not wallet:
            raise Exception("Wallet not found")

        price = Decimal(tariff.price_usdt)
        if wallet.balance_usdt < price:
            raise Exception("NOT_ENOUGH_BALANCE")

        wallet.balance_usdt -= price
        session.add(WalletTransaction(
            wallet_id=wallet.id,
            amount=-price,
            type="extend",
            description=f"Bundle plan renew ({tariff.days} days)"
        ))

        order = Order(
            idUser=user.idUser,
            server_id=servers[0].idServerVPN,
            idTarif=None,
            subscription_id=None,
            bundle_plan_id=plan.id,
            bundle_subscription_id=bundle_sub.id,
            bundle_tariff_id=tariff.id,
            purpose_order="bundle_extension",
            amount=price,
            currency="USDT",
            provider="balance",
            status="processing"
        )
        session.add(order)
        await session.flush()
        session.add(Payment(order_id=order.id,provider="balance",provider_payment_id=f"balance_{order.id}",status="paid"))

        await extend_bundle_subscription(session, bundle_sub, plan, servers, tariff.days)

        now = datetime.utcnow()
        if bundle_sub.expires_at and bundle_sub.expires_at > now:
            bundle_sub.expires_at = bundle_sub.expires_at + timedelta(days=tariff.days)
        else:
            bundle_sub.expires_at = now + timedelta(days=tariff.days)

        bundle_sub.is_active = True
        bundle_sub.status = "active"

        order.status = "completed"
        await session.commit()

        return {
            "subscription_url": bundle_sub.subscription_url,
            "days_added": tariff.days,
            "expires_at_human": rq.format_datetime_ru(bundle_sub.expires_at),
            "plan_name": plan.name
        }


async def create_bundle_subscription(session, user_id: int, plan: BundlePlan, servers: list[ServersVPN], tariff_days: int) -> BundleSubscription:
    sub_id = uuid_lib.uuid4().hex[:16]
    access_token = uuid_lib.uuid4().hex
    expires_at = datetime.utcnow() + timedelta(days=tariff_days)

    bundle_sub = BundleSubscription(
        idUser=user_id,
        bundle_plan_id=plan.id,
        subscription_id=sub_id,
        access_token=access_token,
        subscription_url="",
        created_at=datetime.utcnow(),
        expires_at=expires_at,
        is_active=True,
        status="active"
    )
    session.add(bundle_sub)
    await session.flush()

    subscription_url = rq.build_bundle_subscription_url(access_token)

    for server in servers:
        xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
        inbound = await xui.get_inbound_by_port(server.inbound_port)
        if not inbound:
            raise Exception("Inbound not found")
        client_email = await rq.generate_unique_client_email(session, user_id, server, xui)
        client = await xui.add_client(inbound_id=inbound.id, email=client_email, days=tariff_days, sub_id=sub_id)
        item_sub_id = client.get("sub_id") or sub_id

        session.add(BundleSubscriptionItem(
            bundle_subscription_id=bundle_sub.id,
            server_id=server.idServerVPN,
            client_email=client_email,
            client_uuid=client["uuid"],
            subscription_id=item_sub_id
        ))

    bundle_sub.subscription_id = sub_id
    bundle_sub.subscription_url = subscription_url
    return bundle_sub


async def extend_bundle_subscription(session, bundle_sub: BundleSubscription, plan: BundlePlan, servers: list[ServersVPN], tariff_days: int):
    items = (await session.scalars(
        select(BundleSubscriptionItem).where(BundleSubscriptionItem.bundle_subscription_id == bundle_sub.id)
    )).all()
    items_map = {i.server_id: i for i in items}

    for server in servers:
        item = items_map.get(server.idServerVPN)
        if not item:
            raise Exception("BUNDLE_ITEM_NOT_FOUND")
        xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
        inbound = await xui.get_inbound_by_port(server.inbound_port)
        if not inbound:
            raise Exception("Inbound not found")
        await xui.extend_client(
            inbound_id=inbound.id,
            client_email=item.client_email,
            days=tariff_days,
            sub_id=item.subscription_id or bundle_sub.subscription_id
        )

    now = datetime.utcnow()
    if bundle_sub.expires_at and bundle_sub.expires_at > now:
        bundle_sub.expires_at = bundle_sub.expires_at + timedelta(days=tariff_days)
    else:
        bundle_sub.expires_at = now + timedelta(days=tariff_days)
    bundle_sub.is_active = True
    bundle_sub.status = "active"