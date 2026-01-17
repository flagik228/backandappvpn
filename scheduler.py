from datetime import datetime, timezone

from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from models import async_session, VPNSubscription, ServersVPN
from xui_api import XUIApi
from requestsfile import recalc_server_load


async def expire_vpn_subscriptions():
    """
    Каждые N минут:
    - деактивирует истёкшие VPNSubscription
    - удаляет клиента из 3x-ui
    - обновляет нагрузку сервера
    """
    print("🔁 Running VPN expiration task...")

    now = datetime.now(timezone.utc)

    async with async_session() as session:
        subs = (await session.scalars(
            select(VPNSubscription)
            .where(
                VPNSubscription.is_active == True,
                VPNSubscription.expires_at < now
            )
        )).all()

        if not subs:
            print("✅ No expired VPNs")
            return

        for sub in subs:
            print(f"⛔ Expiring subscription {sub.id}")

            sub.is_active = False
            sub.status = "expired"

            server = await session.get(ServersVPN, sub.idServerVPN)
            if server:
                try:
                    xui = XUIApi(
                        server.api_url,
                        server.xui_username,
                        server.xui_password
                    )
                    inbound = await xui.get_inbound_by_port(server.inbound_port)
                    if inbound:
                        await xui.remove_client(
                            inbound_id=inbound.id,
                            email=sub.provider_client_email
                        )
                        print(f"🗑 Removed client {sub.provider_client_email}")
                except Exception as e:
                    print(f"⚠️ XUI remove error: {e}")

            await recalc_server_load(session, sub.idServerVPN)

        await session.commit()
        print(f"✅ Expired {len(subs)} VPNs")


def start_scheduler():
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        expire_vpn_subscriptions,
        trigger="interval",
        minutes=1,
        id="expire_vpn_task",
        max_instances=1,
        replace_existing=True,
    )

    scheduler.start()
    print("🕒 VPN expiration scheduler started")
