import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import LabeledPrice
from models import async_session, Order, VPNKey, User, ServersVPN
from datetime import datetime, timedelta
from sqlalchemy import select, update, delete

BOT_TOKEN = "8423828272:AAHGuxxQEvTELPukIXl2eNL3p25fI9GGx0U"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)


# Подтверждаем pre_checkout
@dp.pre_checkout_query_handler(lambda q: True)
async def pre_checkout_handler(q: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(q.id, ok=True)


# После успешной оплаты
@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: types.Message):
    payload = message.successful_payment.invoice_payload  # "vpn:<order_id>"
    order_id = int(payload.split(":")[1])

    async with async_session() as session:
        # 1) Получаем заказ
        order = await session.get(Order, order_id)
        if not order:
            await message.reply("Заказ не найден")
            return

        # 2) Проверяем пользователя
        user = await session.get(User, order.idUser)
        if not user:
            await message.reply("Пользователь не найден")
            return

        # 3) Сервер
        server = await session.get(ServersVPN, order.server_id)
        if not server:
            await message.reply("Сервер не найден")
            return

        # 4) Создаём или продлеваем VPNKey
        now = datetime.utcnow()
        vpn_key = await session.scalar(
            select(VPNKey).where(
                VPNKey.idUser == user.idUser,
                VPNKey.idServerVPN == server.idServerVPN
            )
        )

        if vpn_key:
            if vpn_key.expires_at and vpn_key.expires_at > now:
                vpn_key.expires_at += timedelta(days=30)  # Можно заменить на тариф.days
            else:
                vpn_key.expires_at = now + timedelta(days=30)
            vpn_key.is_active = True
        else:
            vpn_key = VPNKey(
                idUser=user.idUser,
                idServerVPN=server.idServerVPN,
                provider="local",
                access_data="generated_access_data",
                created_at=now,
                expires_at=now + timedelta(days=30),
                is_active=True
            )
            session.add(vpn_key)

        # 5) Завершаем заказ
        order.status = "completed"
        session.add(order)
        await session.commit()

        await message.reply(f"Оплата получена! VPN активирован до {vpn_key.expires_at.strftime('%d.%m.%Y')}")


if __name__ == "__main__":
    import asyncio
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
