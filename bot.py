from aiogram import Bot, Dispatcher
from aiogram.types import LabeledPrice
import asyncio
import os

BOT_TOKEN = os.getenv("8423828272:AAHGuxxQEvTELPukIXl2eNL3p25fI9GGx0U")  # ⚠️ ОБЯЗАТЕЛЬНО
PROVIDER_TOKEN = ""  # пусто для Stars

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

async def create_stars_invoice(title, description, payload, amount_stars):
    prices = [LabeledPrice(label=title, amount=amount_stars)]

    invoice = await bot.create_invoice_link(
        title=title,
        description=description,
        payload=payload,
        provider_token=PROVIDER_TOKEN,
        currency="XTR",
        prices=prices
    )
    return invoice


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())