from sqlalchemy import select, exists, func
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException

from models import async_session, User, Order, UserTask, UserReward, VPNSubscription, ServersVPN
from xui_api import XUIApi
from urllib.parse import quote
import requestsfile as rq


TASKS = [
    {
        "key": "welcome_bonus",
        "title": "Приветственный бонус ВСЕМ!",
        "reward_days": 3,
        "check": "check_user_exists"
    },
    {
        "key": "first_purchase",
        "title": "Совершить 1 покупку",
        "reward_days": 1,
        "check": "check_has_orders"
    },
    {
        "key": "first_extension",
        "title": "Совершить 1 продление",
        "reward_days": 2,
        "check": "check_has_extensions_1"
    },
    {
        "key": "second_extension",
        "title": "Совершить 2 продления",
        "reward_days": 10,
        "check": "check_has_extensions_2"
    }
]



# ---------- Условия заданий ----------

async def check_user_exists(user: User) -> bool:
    return True  # сам факт регистрации


async def check_has_orders(user: User) -> bool:
    async with async_session() as session:
        return bool(
            await session.scalar(select(exists().where(
                    Order.idUser == user.idUser,
                    Order.status == "completed",
                    Order.purpose_order == "buy"
                )))
        )


async def _get_extensions_count(user: User) -> int:
    async with async_session() as session:
        count = await session.scalar(select(func.count()).select_from(Order).where(
            Order.idUser == user.idUser,
            Order.status == "completed",
            Order.purpose_order == "extension"
        ))
        return int(count or 0)


async def check_has_extensions_1(user: User) -> bool:
    return await _get_extensions_count(user) >= 1


async def check_has_extensions_2(user: User) -> bool:
    return await _get_extensions_count(user) >= 2


# ---------- Проверка заданий, выдача награды ----------
async def check_and_complete_task(user: User, task: dict):
    async with async_session() as session:

        exists_task = await session.scalar(select(UserTask)
            .where(
                UserTask.idUser == user.idUser,
                UserTask.task_key == task["key"]
            )
        )

        if exists_task:
            return {"status": "already_completed"}

        check_fn = globals()[task["check"]]
        ok = await check_fn(user)

        if not ok:
            return {"status": "not_completed"}

        session.add(UserTask(
            idUser=user.idUser,
            task_key=task["key"]
        ))

        await rq.add_free_days(session, user.idUser, task["reward_days"], "task", meta=task["key"])

        await session.commit()
        return {"status": "completed"}


# ---------- Активация награды ----------
async def activate_reward(user_id: int, reward_id: int, server_id: int):
    async with async_session() as session:

        reward = await session.scalar(select(UserReward).where(
                UserReward.id == reward_id,
                UserReward.idUser == user_id
            ).with_for_update()
        )

        if not reward:
            raise HTTPException(404, "Reward not found")

        if reward.is_activated:
            raise HTTPException(400, "Reward already activated")

        result = await _apply_free_days_to_subscription(session, user_id, server_id, reward.days)

        reward.is_activated = True
        reward.activated_server_id = server_id
        reward.activated_at = datetime.utcnow()

        await session.commit()
        return {"mode": result["mode"], "subscription_id": result["subscription"].id}


async def _apply_free_days_to_subscription(session, user_id: int, server_id: int, days: int, subscription_id: int | None = None):
    server = await session.get(ServersVPN, server_id)
    if not server:
        raise HTTPException(404, "Server not found")

    if subscription_id:
        sub = await session.scalar(select(VPNSubscription)
            .where(
                VPNSubscription.id == subscription_id,
                VPNSubscription.idUser == user_id,
                VPNSubscription.idServerVPN == server_id,
            )
        )
        if not sub:
            raise HTTPException(404, "Subscription not found")
    else:
        sub = await session.scalar(select(VPNSubscription)
            .where(VPNSubscription.idUser == user_id, VPNSubscription.idServerVPN == server_id)
            .order_by(VPNSubscription.created_at.desc())
        )

    now = datetime.now(timezone.utc)

    if sub:
        xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
        inbound = await xui.get_inbound_by_port(server.inbound_port)
        if not inbound:
            raise HTTPException(500, "Inbound not found")

        await xui.extend_client(inbound_id=inbound.id, client_email=sub.provider_client_email, days=days)

        if sub.expires_at and sub.expires_at > now:
            sub.expires_at = sub.expires_at + timedelta(days=days)
        else:
            sub.expires_at = now + timedelta(days=days)

        sub.is_active = True
        sub.status = "active"
        order = Order(
            idUser=user_id,
            server_id=server_id,
            idTarif=None,
            subscription_id=sub.id,
            purpose_order="extension",
            amount=Decimal("0"),
            currency="FREE",
            provider="free_days",
            status="completed",
            created_at=now,
        )
        session.add(order)
        await rq.recalc_server_load(session, server_id)
        return {"mode": "extend", "subscription": sub}

    xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
    client_email = await rq.generate_unique_client_email(session, user_id, server, xui)
    inbound = await xui.get_inbound_by_port(server.inbound_port)
    if not inbound:
        raise HTTPException(500, "Inbound not found")

    client = await xui.add_client(inbound_id=inbound.id, email=client_email, days=days)
    uuid = client["uuid"]

    stream = inbound.stream_settings
    reality = stream.reality_settings
    public_key = reality["settings"]["publicKey"]
    server_name = reality["serverNames"][0]
    short_id = reality["shortIds"][0]

    query = {"type": stream.network, "security": stream.security, "pbk": public_key,
        "fp": "chrome", "sni": server_name, "sid": short_id}
    query_str = "&".join(f"{k}={quote(str(v))}" for k, v in query.items())

    access_link = (
        f"vless://{uuid}@{server.server_ip}:{server.inbound_port}"
        f"?{query_str}#{client_email}"
    )

    expires_at = now + timedelta(days=days)

    sub = VPNSubscription(idUser=user_id, idServerVPN=server_id, provider="xui",
        provider_client_email=client_email, provider_client_uuid=uuid, access_data=access_link,
        created_at=now, expires_at=expires_at, is_active=True, status="active")

    session.add(sub)
    await session.flush()
    order = Order(
        idUser=user_id,
        server_id=server_id,
        idTarif=None,
        subscription_id=sub.id,
        purpose_order="buy",
        amount=Decimal("0"),
        currency="FREE",
        provider="free_days",
        status="completed",
        created_at=now,
    )
    session.add(order)
    await rq.recalc_server_load(session, server_id)
    return {"mode": "create", "subscription": sub}


async def get_free_days_data(user_id: int):
    async with async_session() as session:
        balance = await rq.get_or_create_free_days_balance(session, user_id, for_update=True)
        checkin = await rq.get_or_create_checkin(session, user_id, for_update=True)

        legacy_rewards = (await session.scalars(select(UserReward)
            .where(UserReward.idUser == user_id, UserReward.is_activated == False)
        )).all()

        if legacy_rewards:
            total = sum(r.days for r in legacy_rewards)
            for r in legacy_rewards:
                r.is_activated = True
                r.activated_at = datetime.utcnow()
            if total > 0:
                await rq.add_free_days(session, user_id, total, "legacy_rewards")

        await session.commit()
        await session.refresh(balance)
        await session.refresh(checkin)

        exchange_units = checkin.checkin_count // 10
        return {
            "free_days": balance.balance_days,
            "checkin_count": checkin.checkin_count,
            "exchange_units": exchange_units,
            "exchange_days": exchange_units,
            "last_checkin_at": checkin.last_checkin_at.isoformat() if checkin.last_checkin_at else None,
        }


async def perform_checkin(user_id: int):
    async with async_session() as session:
        checkin = await rq.get_or_create_checkin(session, user_id, for_update=True)
        now = datetime.now(timezone.utc)
        if checkin.last_checkin_at and checkin.last_checkin_at.date() == now.date():
            raise HTTPException(400, "Already checked in today")

        checkin.checkin_count += 1
        checkin.last_checkin_at = now

        await session.commit()
        return {
            "checkin_count": checkin.checkin_count,
            "last_checkin_at": checkin.last_checkin_at.isoformat(),
        }


async def exchange_checkins(user_id: int, checkins: int):
    if checkins <= 0:
        raise HTTPException(400, "Check-ins must be positive")
    async with async_session() as session:
        checkin = await rq.get_or_create_checkin(session, user_id, for_update=True)
        if checkins > checkin.checkin_count:
            raise HTTPException(400, "Not enough check-ins")

        units = checkins // 10
        if units <= 0:
            raise HTTPException(400, "Not enough check-ins")

        if units > (checkin.checkin_count // 10):
            raise HTTPException(400, "Not enough check-ins")

        checkin.checkin_count -= units * 10
        await rq.add_free_days(session, user_id, units, "checkin_exchange", meta=f"checkins:{checkins}")

        await session.commit()
        return {
            "checkin_count": checkin.checkin_count,
            "free_days_added": units,
        }


async def activate_free_days(user_id: int, server_id: int, days: int, subscription_id: int | None = None):
    if days <= 0:
        raise HTTPException(400, "Days must be positive")
    async with async_session() as session:
        balance = await rq.get_or_create_free_days_balance(session, user_id, for_update=True)
        if balance.balance_days < days:
            raise HTTPException(400, "Not enough free days")

        result = await _apply_free_days_to_subscription(session, user_id, server_id, days, subscription_id=subscription_id)
        try:
            await rq.deduct_free_days(session, user_id, days, "activate", meta=f"server_id:{server_id}")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        await session.commit()
        await session.refresh(balance)

        sub = result["subscription"]
        return {
            "mode": result["mode"],
            "subscription_id": sub.id,
            "expires_at": sub.expires_at.isoformat(),
            "expires_at_human": rq.format_datetime_ru(sub.expires_at),
            "free_days_left": balance.balance_days,
        }