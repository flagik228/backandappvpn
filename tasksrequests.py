from sqlalchemy import select, exists
from sqlalchemy.exc import NoResultFound
from datetime import datetime, timedelta
from fastapi import HTTPException

from models import async_session, User, Order, UserTask, UserReward, VPNKey, VPNSubscription, ServersVPN
from requestsfile import pay_and_extend_vpn, create_vpn_xui
from xui_api import XUIApi


TASKS = [
    {
        "key": "welcome_bonus",
        "title": "Приветственный бонус ВСЕМ!",
        "reward_days": 1,
        "check": "check_user_exists"
    },
    {
        "key": "first_purchase",
        "title": "Совершить 1 покупку",
        "reward_days": 1,
        "check": "check_has_orders"
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

        session.add(UserReward(
            idUser=user.idUser,
            reward_type="vpn_days",
            days=task["reward_days"]
        ))

        await session.commit()
        return {"status": "completed"}


# ---------- Активация награды ----------
async def activate_reward(user_id: int, reward_id: int, server_id: int):
    async with async_session() as session:
        # async with session.begin():

        reward = await session.scalar(select(UserReward).where(
                UserReward.id == reward_id,
                UserReward.idUser == user_id
            ).with_for_update()
        )

        if not reward:
            raise HTTPException(404, "Reward not found")

        if reward.is_activated:
            raise HTTPException(400, "Reward already activated")

        server = await session.get(ServersVPN, server_id)
        if not server:
            raise HTTPException(404, "Server not found")

        vpn_key = await session.scalar(select(VPNKey).where(
                VPNKey.idUser == user_id,
                VPNKey.idServerVPN == server_id
            )
        )

        # ===== EXTEND =====
        if vpn_key:
            xui = XUIApi(server.api_url, server.xui_username, server.xui_password)
            inbound = await xui.get_inbound_by_port(server.inbound_port)

            await xui.extend_client(
                inbound_id=inbound.id,
                client_email=vpn_key.provider_client_email,
                days=reward.days
            )

            now = datetime.utcnow()
            vpn_key.expires_at = (
                vpn_key.expires_at + timedelta(days=reward.days)
                if vpn_key.expires_at and vpn_key.expires_at > now
                else now + timedelta(days=reward.days)
            )
            vpn_key.is_active = True

            sub = await session.scalar(select(VPNSubscription).where(VPNSubscription.vpn_key_id == vpn_key.id))
            if sub:
                sub.expires_at = vpn_key.expires_at
                sub.status = "active"

        # ===== CREATE =====
        else:
            await create_vpn_xui(
                user_id=user_id,
                server_id=server_id,
                tariff_days=reward.days
            )

        # ✅ ПОМЕЧАЕМ НАГРАДУ ИСПОЛЬЗОВАННОЙ
        reward.is_activated = True
        reward.activated_server_id = server_id
        reward.activated_at = datetime.utcnow()

    # 🔥 ВАЖНО
    await session.commit()

