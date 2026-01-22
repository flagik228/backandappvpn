from datetime import datetime, timezone

from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from models import async_session, VPNSubscription, Order, User
from main import bot


"""Находит активные VPN-подписки с истёкшим expires_at, помечает их как:
        is_active = False
        status = "expired" """
async def update_vpn_subscription_statuses():
    print("🔁 Running VPN subscription status updater...")
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.scalars(select(VPNSubscription)
            .where(VPNSubscription.is_active == True,VPNSubscription.expires_at < now))

        expired_subs = result.all()

        if not expired_subs:
            print("✅ No expired subscriptions found")
            return

        for sub in expired_subs:
            print(f"⛔ Marking subscription {sub.id} as expired")
            sub.is_active = False
            sub.status = "expired"

        await session.commit()
        print(f"✅ Updated {len(expired_subs)} subscription(s)")


async def expire_orders_task():
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        orders = (await session.scalars(select(Order).where(
            Order.status == "pending",Order.expires_at.isnot(None),Order.expires_at < now))).all()

        if not orders:
            return

        for o in orders:
            o.status = "expired"
        
        user = await session.get(User, o.idUser)
        if user:
            try:
                await bot.send_message(chat_id=user.tg_id,
                    text="⏳ Мы не дождались оплату, заказ истёк. Но можно создать новый))")
            except Exception:
                pass

        await session.commit()
        print(f"🧾 Expired {len(orders)} pending orders")


def start_scheduler():
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(update_vpn_subscription_statuses,trigger="interval",minutes=5,id="vpn_status_updater",
        max_instances=1,replace_existing=True,coalesce=True) # если пропустили тики — выполнит один раз

    scheduler.add_job(expire_orders_task,trigger="interval",seconds=30,id="expire_orders_task",
        max_instances=1,replace_existing=True,)

    scheduler.start()
    print("🕒 VPN subscription status scheduler started")
    print("🕒 Scheduler started (orders expiration)")
