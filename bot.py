import asyncio
from aiogram import Bot, Dispatcher, types
from models import async_session, Order, VPNKey, User, ServersVPN
from datetime import datetime, timedelta
from sqlalchemy import select

BOT_TOKEN = "8423828272:AAHGuxxQEvTELPukIXl2eNL3p25fI9GGx0U"

async def remove_webhook():
    bot = Bot(BOT_TOKEN)
    await bot.delete_webhook()
    await bot.session.close()

asyncio.run(remove_webhook())

dp = Dispatcher()


# --- PreCheckoutQuery ---
async def pre_checkout_handler(q: types.PreCheckoutQuery):
    await q.answer(ok=True)

dp.pre_checkout_query.register(pre_checkout_handler)


# --- Успешная оплата ---
async def successful_payment_handler(message: types.Message):
    # Проверяем, что это именно успешная оплата
    if not message.successful_payment:
        return

    payload = message.successful_payment.invoice_payload  # "vpn:<order_id>"
    order_id = int(payload.split(":")[1])

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            await message.reply("Заказ не найден")
            return

        user = await session.get(User, order.idUser)
        if not user:
            await message.reply("Пользователь не найден")
            return

        server = await session.get(ServersVPN, order.server_id)
        if not server:
            await message.reply("Сервер не найден")
            return

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

        order.status = "completed"
        session.add(order)
        await session.commit()

        await message.reply(f"Оплата получена! VPN активирован до {vpn_key.expires_at.strftime('%d.%m.%Y')}")


# --- Регистрируем хендлер через фильтр lambda ---
dp.message.register(successful_payment_handler, lambda msg: msg.successful_payment is not None)


# --- Главная функция ---
async def main():
    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
