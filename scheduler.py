import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from models import async_session, VPNKey, VPNSubscription, ServersVPN
from xui_api import XUIApi
from requestsfile import recalc_server_load


async def expire_vpn_subscriptions():
    """
    Каждые 30 минут:
    - деактивирует истёкшие VPN
    - удаляет клиента из 3x-ui
    - обновляет нагрузку сервера
    """
    print("🔁 Running VPN expiration task...")

    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.scalars(
            select(VPNKey).where(
                VPNKey.is_active == True,
                VPNKey.expires_at < now
            )
        )

        expired_keys = result.all()

        if not expired_keys:
            print("✅ No expired VPNs")
            return

        for key in expired_keys:
            print(f"⛔ Expiring VPN key {key.id}")

            # 1️⃣ деактивируем ключ
            key.is_active = False

            # 2️⃣ обновляем подписку
            sub = await session.scalar(
                select(VPNSubscription)
                .where(VPNSubscription.vpn_key_id == key.id)
            )
            if sub:
                sub.status = "expired"

            # 3️⃣ удаляем клиента из XUI
            server = await session.get(ServersVPN, key.idServerVPN)
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
                            email=key.provider_client_email
                        )
                        print(f"🗑 Removed client {key.provider_client_email} from XUI")
                except Exception as e:
                    print(f"⚠️ XUI remove error: {e}")

            # 4️⃣ пересчёт нагрузки сервера
            await recalc_server_load(session, key.idServerVPN)

        await session.commit()
        print(f"✅ Expired {len(expired_keys)} VPNs")


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
