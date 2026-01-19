from decimal import Decimal
from sqlalchemy import select
from models import (
    User, UserWallet, WalletOperation, WalletTransaction,
    Order, Payment, ExchangeRate
)
from models import async_session


# =========================
# Получить кошелёк
async def get_user_wallet(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return None

        wallet = await session.scalar(
            select(UserWallet).where(UserWallet.idUser == user.idUser)
        )

        return {
            "balance_usdt": str(wallet.balance_usdt)
        }


# =========================
# Создание пополнения (Stars)
async def create_stars_deposit(tg_id: int, amount_usdt: Decimal):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("User not found")

        rate = await session.scalar(
            select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT")
        )
        if not rate:
            raise Exception("Exchange rate not set")

        stars_amount = int(amount_usdt / rate.rate)
        if stars_amount < 1:
            stars_amount = 1

        op = WalletOperation(
            idUser=user.idUser,
            type="deposit",
            amount_usdt=amount_usdt,
            provider="stars",
            status="pending"
        )
        session.add(op)
        await session.flush()
        await session.commit()

        return {
            "wallet_operation_id": op.id,
            "stars_amount": stars_amount
        }


# =========================
# Создание пополнения (CryptoBot)
async def create_crypto_deposit(tg_id: int, amount_usdt: Decimal):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise Exception("User not found")

        if amount_usdt < Decimal("0.1"):
            raise Exception("Minimum amount is 0.1 USDT")

        op = WalletOperation(
            idUser=user.idUser,
            type="deposit",
            amount_usdt=amount_usdt,
            provider="cryptobot",
            status="pending"
        )
        session.add(op)
        await session.flush()
        await session.commit()

        return {
            "wallet_operation_id": op.id,
            "amount_usdt": str(amount_usdt)
        }


# =========================
# Завершение пополнения
async def complete_wallet_deposit(session, wallet_operation_id: int):
    op = await session.get(WalletOperation, wallet_operation_id)
    if not op or op.status != "pending":
        return

    wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == op.idUser))

    wallet.balance_usdt += op.amount_usdt
    op.status = "completed"

    tx = WalletTransaction(
        wallet_id=wallet.id,
        amount=op.amount_usdt,
        type="deposit",
        description="Wallet top-up"
    )
    session.add(tx)