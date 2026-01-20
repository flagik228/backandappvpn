from datetime import datetime, timezone

from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from models import async_session, VPNSubscription


async def update_vpn_subscription_statuses():
    """
    Периодическая задача:
    - находит активные VPN-подписки с истёкшим expires_at
    - помечает их как:
        is_active = False
        status = "expired"
    """

    print("🔁 Running VPN subscription status updater...")

    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.scalars(select(VPNSubscription)
            .where(
                VPNSubscription.is_active == True,
                VPNSubscription.expires_at < now
            )
        )

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


def start_scheduler():
    """
    Запуск APScheduler.
    Вызывать ОДИН раз при старте приложения (например, в lifespan / startup).
    """

    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        update_vpn_subscription_statuses,
        trigger="interval",
        minutes=5,              # можно увеличить до 5–10 мин без проблем
        id="vpn_status_updater",
        max_instances=1,
        replace_existing=True,
        coalesce=True,          # если пропустили тики — выполнит один раз
    )

    scheduler.start()

    print("🕒 VPN subscription status scheduler started")
