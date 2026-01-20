from sqlalchemy import select, update, delete
from models import (async_session, User, UserWallet, WalletTransaction, VPNSubscription, TypesVPN,
    CountriesVPN, ServersVPN, Tariff, ExchangeRate, Order, Payment, ReferralConfig, ReferralEarning)
from typing import List
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import select, func, exists
from sqlalchemy.orm import aliased
from urllib.parse import quote
from xui_api import XUIApi


# =======================
# --- USERS ---
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
        await session.commit()
        await session.refresh(user)
        return user
    

async def get_user_wallet(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return None

        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user.idUser))
        return {"balance_usdt": str(wallet.balance_usdt)}
        
    
# =======================
# --- SERVERS ---
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
        "api_url": s.api_url,
        "xui_username": s.xui_username,
        "xui_password": s.xui_password,
        "inbound_port": s.inbound_port
        }
        
        
async def get_servers_full():
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN).where(ServersVPN.is_active == True))
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        rate_val = rate.rate if rate else Decimal("1")

        result = []
        for s in servers:
            # Получаем тарифы
            tariffs_rows = await session.scalars(select(Tariff).where(Tariff.server_id == s.idServerVPN, Tariff.is_active == True))
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
                "type_description": type_vpn.descriptionType if type_vpn else "", #
                "country": country.nameCountry if country else "",
                "tariffs": tariffs_list
            })
        return result


# =======================
# --- GET TARIFFS ---
async def get_server_tariffs(server_id: int):
    async with async_session() as session:
        tariffs = await session.scalars(select(Tariff).where(Tariff.server_id == server_id, Tariff.is_active == True))
        return [{"idTarif": t.idTarif,"days": t.days,"price_usdt": str(t.price_tarif)
        } for t in tariffs]


# изменение now_conn сервера
async def recalc_server_load(session, server_id: int):
    server = await session.get(ServersVPN, server_id)
    active_count = await session.scalar(select(func.count()).select_from(VPNSubscription).where(
            VPNSubscription.idServerVPN == server_id,
            VPNSubscription.is_active == True,
            VPNSubscription.expires_at > datetime.now(timezone.utc)
        )
    )
    server.now_conn = active_count
    server.is_active = active_count < server.max_conn

        
# форматирование даты 2026-01-04 22:46
def format_datetime_ru(dt: datetime) -> str:
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M")


# =======================
# --- USER: MY VPNs ---
async def get_my_vpns(tg_id: int) -> List[dict]:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return []

        now = datetime.now(timezone.utc)
        rows = await session.execute(select(VPNSubscription, ServersVPN)
            .join(ServersVPN, VPNSubscription.idServerVPN == ServersVPN.idServerVPN)
            .where(VPNSubscription.idUser == user.idUser)
            .order_by(VPNSubscription.expires_at.desc())
        )

        result = []
        for sub, server in rows:
            is_active = sub.expires_at > now
            result.append({
                "subscription_id": sub.id,
                "server_id": server.idServerVPN,
                "serverName": server.nameVPN,
                "access_data": sub.access_data,
                "expires_at": sub.expires_at.isoformat(),
                "is_active": is_active,
                "status": "active" if is_active else "expired"
            })

        return result


#статус подписки в профиле
async def has_active_subscription(tg_id: int) -> bool:
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return False

        q = select(exists().where(
                VPNSubscription.idUser == user.idUser,
                VPNSubscription.is_active == True,
                VPNSubscription.expires_at > datetime.now(timezone.utc)
            )
        )

        return bool(await session.scalar(q))


# --- Генерация уникального email для клиента <COUNTRY>-<TGID>-<N>@artcry
async def generate_unique_client_email(session,user_id: int,server: ServersVPN,xui: XUIApi) -> str:
    country = await session.get(CountriesVPN, server.idCountry)
    country_code = country.nameCountry.upper()[:3]

    inbound = await xui.get_inbound_by_port(server.inbound_port)
    if not inbound:
        raise Exception("Inbound not found")

    # считаем клиентов в XUI
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


# =======================
# --- REFERRALS ---
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

        rows = await session.execute(select(
                ReferralUser.idUser,
                ReferralUser.tg_username,
                func.coalesce(func.sum(ReferralEarning.amount_usdt), 0)
            )
            .outerjoin(Order,Order.idUser == ReferralUser.idUser)
            .outerjoin(ReferralEarning, ReferralEarning.order_id == Order.id)
            .where(ReferralUser.referrer_id == referrer.idUser)
            .group_by(ReferralUser.idUser, ReferralUser.tg_username)
            .order_by(ReferralUser.created_at.desc())
        )

        return [{"idUser": r.idUser,"username": r.tg_username,"total_earned": str(r[2])}
            for r in rows
        ]


# =======================
# --- REFERRAL PAYOUT ---
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

    tx = WalletTransaction(wallet_id=wallet.id,amount=reward_usdt,type="referral",
        description=f"Реферальное начисление {percent}% (+${reward_usdt})"
    )
    session.add(tx)