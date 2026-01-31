import os
from sqlalchemy import select, update, delete
from models import (async_session, User, UserWallet, WalletOperation, WalletTransaction, VPNSubscription, TypesVPN,
    CountriesVPN, ServersVPN, Tariff, ExchangeRate, Order, Payment, ReferralConfig, ReferralEarning,
    UserFreeDaysBalance, UserRewardOp, UserCheckin, PromoCode, PromoCodeUsage, BundlePlan, BundleSubscription,
    BundleTariff)
from typing import List
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import select, func, exists
from sqlalchemy.orm import aliased
from urllib.parse import quote, urlparse
from xui_api import XUIApi

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://artcryvpnbot.lunaweb.ru").rstrip("/")


# USERS
async def add_user(tg_id: int, user_role: str = "user", referrer_id: int | None = None):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user:
            return user
        user = User(tg_id=tg_id,userRole=user_role,referrer_id=referrer_id)
        session.add(user)
        await session.flush()
        wallet = UserWallet(idUser=user.idUser, balance_usdt=Decimal("0.00"))
        session.add(wallet)

        if referrer_id:
            referrer = await session.get(User, referrer_id)
            if referrer:
                await add_free_days(session, referrer.idUser, 1, "referral_signup", meta=f"referred_user:{user.idUser}")

        await session.commit()
        await session.refresh(user)
        return user


def normalize_promo_code(code: str) -> str:
    return code.strip().upper()


async def validate_promo_code(user_id: int, code: str):
    if not code:
        return {"valid": False, "reason": "empty"}
    code_norm = normalize_promo_code(code)
    async with async_session() as session:
        promo = await session.scalar(
            select(PromoCode).where(
                PromoCode.code_normalized == code_norm,
                PromoCode.is_active == True
            )
        )
        if not promo:
            return {"valid": False, "reason": "not_found"}

        used = await session.scalar(
            select(PromoCodeUsage).where(
                PromoCodeUsage.promo_code_id == promo.id,
                PromoCodeUsage.idUser == user_id
            )
        )
        if used:
            return {"valid": False, "reason": "already_used"}

        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            return {"valid": False, "reason": "limit"}

        reward = {
            "type": promo.reward_type,
            "value": str(promo.reward_value),
            "name": promo.reward_name
        }
        return {"valid": True, "reward": reward}


async def apply_promo_code(user_id: int, code: str):
    if not code:
        return {"ok": False, "reason": "empty"}
    code_norm = normalize_promo_code(code)
    async with async_session() as session:
        promo = await session.scalar(
            select(PromoCode)
            .where(PromoCode.code_normalized == code_norm, PromoCode.is_active == True)
            .with_for_update()
        )
        if not promo:
            return {"ok": False, "reason": "not_found"}

        used = await session.scalar(
            select(PromoCodeUsage)
            .where(PromoCodeUsage.promo_code_id == promo.id, PromoCodeUsage.idUser == user_id)
        )
        if used:
            return {"ok": False, "reason": "already_used"}

        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            return {"ok": False, "reason": "limit"}

        if promo.reward_type == "balance":
            wallet = await session.scalar(
                select(UserWallet).where(UserWallet.idUser == user_id).with_for_update()
            )
            if not wallet:
                return {"ok": False, "reason": "wallet_not_found"}
            wallet.balance_usdt += promo.reward_value
            session.add(WalletTransaction(
                wallet_id=wallet.id,
                amount=promo.reward_value,
                type="promo",
                description=f"Промокод {promo.code}"
            ))
        elif promo.reward_type == "free_days":
            days = int(promo.reward_value)
            await add_free_days(session, user_id, days, "promo", meta=f"code:{promo.code}")
        else:
            return {"ok": False, "reason": "invalid_reward"}

        promo.used_count += 1
        session.add(PromoCodeUsage(promo_code_id=promo.id, idUser=user_id))
        await session.commit()

        reward = {
            "type": promo.reward_type,
            "value": str(promo.reward_value),
            "name": promo.reward_name
        }
        return {"ok": True, "reward": reward}


async def get_or_create_free_days_balance(session, user_id: int, for_update: bool = False) -> UserFreeDaysBalance:
    query = select(UserFreeDaysBalance).where(UserFreeDaysBalance.idUser == user_id)
    if for_update:
        query = query.with_for_update()
    balance = await session.scalar(query)
    if balance:
        return balance
    balance = UserFreeDaysBalance(idUser=user_id, balance_days=0)
    session.add(balance)
    await session.flush()
    return balance


async def add_free_days(session, user_id: int, days: int, source: str, meta: str | None = None):
    if days <= 0:
        return
    balance = await get_or_create_free_days_balance(session, user_id, for_update=True)
    balance.balance_days += days
    balance.updated_at = datetime.utcnow()
    session.add(UserRewardOp(idUser=user_id, source=source, days_delta=days, meta=meta))


async def deduct_free_days(session, user_id: int, days: int, source: str, meta: str | None = None):
    if days <= 0:
        raise ValueError("Days must be positive")
    balance = await get_or_create_free_days_balance(session, user_id, for_update=True)
    if balance.balance_days < days:
        raise ValueError("Not enough free days")
    balance.balance_days -= days
    balance.updated_at = datetime.utcnow()
    session.add(UserRewardOp(idUser=user_id, source=source, days_delta=-days, meta=meta))


async def get_or_create_checkin(session, user_id: int, for_update: bool = False) -> UserCheckin:
    query = select(UserCheckin).where(UserCheckin.idUser == user_id)
    if for_update:
        query = query.with_for_update()
    checkin = await session.scalar(query)
    if checkin:
        return checkin
    checkin = UserCheckin(idUser=user_id, checkin_count=0)
    session.add(checkin)
    await session.flush()
    return checkin
    

async def get_user_wallet(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return None

        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.idUser))
        return {"balance_usdt": str(wallet.balance_usdt)}


def _history_ts(dt: datetime | None) -> float:
    if not dt:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


async def get_user_history(tg_id: int, limit: int = 200):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.idUser))

        orders = (await session.scalars(
            select(Order)
            .where(Order.idUser == user.idUser)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )).all()

        operations = (await session.scalars(
            select(WalletOperation)
            .where(WalletOperation.idUser == user.idUser)
            .order_by(WalletOperation.created_at.desc())
            .limit(limit)
        )).all()

        wallet_txs = []
        if wallet:
            wallet_txs = (await session.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == wallet.id, WalletTransaction.type.in_(["referral", "promo"]))
                .order_by(WalletTransaction.created_at.desc())
                .limit(limit)
            )).all()

        items = []
        for o in orders:
            items.append((_history_ts(o.created_at), {
                "id": o.id,
                "source": "order",
                "purpose": o.purpose_order,
                "status": o.status,
                "amount_usdt": str(o.amount),
                "provider": o.provider,
                "created_at": o.created_at.isoformat() if o.created_at else None
            }))

        for op in operations:
            if op.type != "deposit":
                continue
            items.append((_history_ts(op.created_at), {
                "id": op.id,
                "source": "wallet_operation",
                "purpose": op.type,
                "status": op.status,
                "amount_usdt": str(op.amount_usdt),
                "provider": op.provider,
                "created_at": op.created_at.isoformat() if op.created_at else None
            }))

        for t in wallet_txs:
            items.append((_history_ts(t.created_at), {
                "id": t.id,
                "source": "wallet_transaction",
                "purpose": t.type,
                "status": "completed",
                "amount_usdt": str(t.amount),
                "description": t.description,
                "created_at": t.created_at.isoformat() if t.created_at else None
            }))

        items.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in items[:limit]]


# SERVERS x tarifs
async def get_servers() -> List[dict]:
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN).where(ServersVPN.is_active == True))
        return [{"idServerVPN": s.idServerVPN,"nameVPN": s.nameVPN,"price_usdt": str(s.price_usdt),
            "max_conn": s.max_conn,"now_conn": s.now_conn,"server_ip": s.server_ip,"api_url": s.api_url
        } for s in servers]


async def get_server_by_id(server_id: int):
    async with async_session() as session:
        s = await session.get(ServersVPN, server_id)
        if not s:
            return None
        return {"idServerVPN": s.idServerVPN,"nameVPN": s.nameVPN,"price_usdt": str(s.price_usdt),
        "api_url": s.api_url,"xui_username": s.xui_username,"xui_password": s.xui_password,"inbound_port": s.inbound_port}
        
        
async def get_servers_full():
    async with async_session() as session:
        rows = await session.execute(
            select(ServersVPN, TypesVPN, CountriesVPN)
            .join(TypesVPN, ServersVPN.idTypeVPN == TypesVPN.idTypeVPN, isouter=True)
            .join(CountriesVPN, ServersVPN.idCountry == CountriesVPN.idCountry, isouter=True)
            .where(ServersVPN.is_active == True)
        )
        servers = rows.all()
        server_ids = [s.idServerVPN for s, _, _ in servers]
        tariffs = []
        if server_ids:
            tariffs = await session.scalars(
                select(Tariff)
                .where(Tariff.server_id.in_(server_ids), Tariff.is_active == True)
            )
        tariffs_map = {}
        for t in tariffs:
            tariffs_map.setdefault(t.server_id, []).append(t)

        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        rate_val = rate.rate if rate else Decimal("1")

        result = []
        for s, type_vpn, country in servers:
            tariffs_rows = tariffs_map.get(s.idServerVPN, [])
            tariffs_list = []
            for t in tariffs_rows:
                tariffs_list.append({"idTarif": t.idTarif,"days": t.days,
                    "price_usdt": str(t.price_tarif),"price_stars": int(t.price_tarif / rate_val)})

            result.append({"idServerVPN": s.idServerVPN,"nameVPN": s.nameVPN,"type_vpn": type_vpn.nameType if type_vpn else "",
                "type_description": type_vpn.descriptionType if type_vpn else "","country": country.nameCountry if country else "","tariffs": tariffs_list})
        return result


async def get_server_tariffs(server_id: int):
    async with async_session() as session:
        tariffs = await session.scalars(select(Tariff).where(Tariff.server_id == server_id, Tariff.is_active == True))
        return [{"idTarif": t.idTarif,"days": t.days,"price_usdt": str(t.price_tarif)
        } for t in tariffs]


async def recalc_server_load(session, server_id: int):
    server = await session.get(ServersVPN, server_id)
    active_count = await session.scalar(select(func.count()).select_from(VPNSubscription).where(VPNSubscription.idServerVPN == server_id,
        VPNSubscription.is_active == True,VPNSubscription.expires_at > datetime.now(timezone.utc)))
    server.now_conn = active_count
    server.is_active = active_count < server.max_conn

        
def format_datetime_ru(dt: datetime) -> str:
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M")


def build_subscription_url(server: ServersVPN, sub_id: str | None) -> str | None:
    if not sub_id:
        return None
    scheme = "https"
    host = None
    if server.api_url:
        parsed = urlparse(server.api_url)
        if parsed.scheme:
            scheme = parsed.scheme
        if parsed.hostname:
            host = parsed.hostname
    if not host and server.server_ip:
        host = server.server_ip
    if not host:
        return None
    port = server.subscription_port or 2096
    return f"{scheme}://{host}:{port}/sub/{sub_id}"


def build_bundle_subscription_url(bundle_sub_id: int) -> str:
    return f"{PUBLIC_BASE_URL}/api/vpn/bundle/sub/{bundle_sub_id}"


# MY VPNs
async def get_my_vpns(tg_id: int) -> List[dict]:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        now = datetime.now(timezone.utc)
        rows = await session.execute(select(VPNSubscription, ServersVPN).join(ServersVPN, VPNSubscription.idServerVPN == ServersVPN.idServerVPN)
            .where(VPNSubscription.idUser == user.idUser).order_by(VPNSubscription.expires_at.desc()))

        result = []
        for sub, server in rows:
            is_active = sub.expires_at > now
            subscription_url = build_subscription_url(server, sub.subscription_id) or sub.subscription_url
            result.append({"subscription_id": sub.id,"server_id": server.idServerVPN,"serverName": server.nameVPN,
                "subscription_url": subscription_url,"subscription_key": sub.subscription_id,
                "expires_at": sub.expires_at.isoformat(),"is_active": is_active,"status": "active" if is_active else "expired"})

        return result


async def get_subscriptions_by_server(user_id: int, server_id: int) -> List[dict]:
    async with async_session() as session:
        server = await session.get(ServersVPN, server_id)
        if not server:
            return []

        now = datetime.now(timezone.utc)
        subs = (await session.scalars(
            select(VPNSubscription)
            .where(VPNSubscription.idUser == user_id, VPNSubscription.idServerVPN == server_id)
            .order_by(VPNSubscription.expires_at.desc())
        )).all()

        result = []
        for sub in subs:
            is_active = sub.expires_at > now
            delta = sub.expires_at - now
            days_left = max(0, (delta.days + (1 if delta.seconds > 0 else 0)))
            subscription_url = build_subscription_url(server, sub.subscription_id) or sub.subscription_url
            result.append({
                "subscription_id": sub.id,
                "server_id": server.idServerVPN,
                "server_name": server.nameVPN,
                "is_active": is_active,
                "status": "active" if is_active else "expired",
                "expires_at": sub.expires_at.isoformat(),
                "expires_at_human": format_datetime_ru(sub.expires_at),
                "days_left": days_left,
                "subscription_url": subscription_url,
                "subscription_key": sub.subscription_id,
            })
        return result


async def get_bundle_plans_active() -> List[dict]:
    async with async_session() as session:
        plans = (await session.scalars(select(BundlePlan).where(BundlePlan.is_active == True))).all()
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        rate_val = rate.rate if rate else Decimal("1")
        result = []
        for p in plans:
            tariffs = (await session.scalars(
                select(BundleTariff)
                .where(BundleTariff.bundle_plan_id == p.id, BundleTariff.is_active == True)
            )).all()
            tariffs_data = []
            for t in tariffs:
                price_stars = int(Decimal(t.price_usdt) / rate_val)
                if price_stars < 1:
                    price_stars = 1
                tariffs_data.append({
                    "id": t.id,
                    "days": t.days,
                    "price_usdt": str(t.price_usdt),
                    "price_stars": price_stars
                })
            result.append({
                "id": p.id,
                "name": p.name,
                "price_usdt": str(p.price_usdt),
                "days": p.days,
                "tariffs": tariffs_data
            })
        return result


async def get_my_bundle_vpns(tg_id: int) -> List[dict]:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        now = datetime.now(timezone.utc)
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        rate_val = rate.rate if rate else Decimal("1")
        rows = await session.execute(
            select(BundleSubscription, BundlePlan)
            .join(BundlePlan, BundleSubscription.bundle_plan_id == BundlePlan.id)
            .where(BundleSubscription.idUser == user.idUser)
            .order_by(BundleSubscription.expires_at.desc())
        )

        result = []
        for sub, plan in rows:
            is_active = sub.expires_at > now
            tariffs = (await session.scalars(
                select(BundleTariff)
                .where(BundleTariff.bundle_plan_id == plan.id, BundleTariff.is_active == True)
            )).all()
            tariffs_data = []
            for t in tariffs:
                price_stars = int(Decimal(t.price_usdt) / rate_val)
                if price_stars < 1:
                    price_stars = 1
                tariffs_data.append({
                    "id": t.id,
                    "days": t.days,
                    "price_usdt": str(t.price_usdt),
                    "price_stars": price_stars
                })
            result.append({
                "bundle_subscription_id": sub.id,
                "plan_id": plan.id,
                "plan_name": plan.name,
                "plan_days": plan.days,
                "plan_price_usdt": str(plan.price_usdt),
                "subscription_url": sub.subscription_url,
                "expires_at": sub.expires_at.isoformat(),
                "is_active": is_active,
                "status": "active" if is_active else "expired",
                "tariffs": tariffs_data
            })
        return result


async def has_active_subscription(tg_id: int) -> bool:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return False

        now = datetime.now(timezone.utc)
        q = select(exists().where(
            VPNSubscription.idUser == user.idUser,
            VPNSubscription.is_active == True,
            VPNSubscription.expires_at > now
        ))
        bundle_q = select(exists().where(
            BundleSubscription.idUser == user.idUser,
            BundleSubscription.is_active == True,
            BundleSubscription.expires_at > now
        ))

        return bool(await session.scalar(q)) or bool(await session.scalar(bundle_q))


async def generate_unique_client_email(session,user_id: int,server: ServersVPN,xui: XUIApi) -> str:
    country = await session.get(CountriesVPN, server.idCountry)
    country_code = country.nameCountry.upper()[:3]

    inbound = await xui.get_inbound_by_port(server.inbound_port)
    if not inbound:
        raise Exception("Inbound not found")

    existing = inbound.settings.clients or []

    prefix = f"{country_code}-{user_id}-"
    nums = []
    for c in existing:
        if c.email.startswith(prefix):
            try:
                nums.append(int(c.email.split("-")[-1].split("@")[0]))
            except:
                pass

    next_num = max(nums) + 1 if nums else 1
    return f"{prefix}{next_num}@artcry"


# REFERRALS
async def get_referrals_count(tg_id: int) -> int:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return 0

        count = await session.scalar(select(func.count()).select_from(User).where(User.referrer_id == user.idUser))
        return count or 0


async def get_referrals_list(tg_id: int):
    async with async_session() as session:
        referrer = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not referrer:
            return []

        ReferralUser = aliased(User)

        rows = await session.execute(select(ReferralUser.idUser,ReferralUser.tg_username,func.coalesce(func.sum(ReferralEarning.amount_usdt), 0))
            .outerjoin(Order,Order.idUser == ReferralUser.idUser)
            .outerjoin(ReferralEarning, ReferralEarning.order_id == Order.id)
            .where(ReferralUser.referrer_id == referrer.idUser)
            .group_by(ReferralUser.idUser, ReferralUser.tg_username)
            .order_by(ReferralUser.created_at.desc())
        )

        return [{"idUser": r.idUser,"username": r.tg_username,"total_earned": str(r[2])}
            for r in rows
        ]


# REFERRAL PAYOUT
async def process_referral_reward(session, order: Order):
    user = await session.get(User, order.idUser)
    if not user or not user.referrer_id:
        return  # не реферал
    config = await session.scalar(select(ReferralConfig).where(ReferralConfig.is_active == True))
    if not config:
        return
    tariff = await session.get(Tariff, order.idTarif)
    if not tariff:
        return

    percent = config.percent
    base_usdt = Decimal(tariff.price_tarif) # 🔥 ВСЕГДА считаем от USDT-цены тарифа
    reward_usdt = (base_usdt * Decimal(percent) / Decimal(100)).quantize(Decimal("0.000001"))

    wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.referrer_id))
    if not wallet:
        return

    wallet.balance_usdt += reward_usdt
    earning = ReferralEarning(referrer_id=user.referrer_id,order_id=order.id,percent=percent,amount_usdt=reward_usdt)
    session.add(earning)

    tx = WalletTransaction(wallet_id=wallet.id,amount=reward_usdt,type="referral",description=f"Реферальное начисление {percent}% (+${reward_usdt})")
    session.add(tx)