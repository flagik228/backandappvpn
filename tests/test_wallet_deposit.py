import os
import unittest
from decimal import Decimal

from sqlalchemy import delete, select

from models import User, UserWallet, WalletOperation, async_session
import walletrequests as wrq


class WalletDepositTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        required = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME"]
        if not all(os.getenv(k) for k in required):
            raise unittest.SkipTest("Database env vars not set")

    async def asyncSetUp(self):
        async with async_session() as session:
            user = User(tg_id=999000111, tg_username="test_wallet", userRole="user")
            session.add(user)
            await session.flush()

            wallet = UserWallet(idUser=user.idUser, balance_usdt=Decimal("0"))
            op = WalletOperation(
                idUser=user.idUser,
                type="deposit",
                amount_usdt=Decimal("1.5"),
                provider="stars",
                status="pending",
            )
            session.add_all([wallet, op])
            await session.commit()

            self.user_id = user.idUser
            self.op_id = op.id

    async def asyncTearDown(self):
        async with async_session() as session:
            await session.execute(delete(WalletOperation).where(WalletOperation.id == self.op_id))
            await session.execute(delete(UserWallet).where(UserWallet.idUser == self.user_id))
            await session.execute(delete(User).where(User.idUser == self.user_id))
            await session.commit()

    async def test_complete_wallet_deposit_idempotent(self):
        async with async_session() as session:
            await wrq.complete_wallet_deposit(session, self.op_id)
            await session.commit()

        async with async_session() as session:
            wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == self.user_id))
            op = await session.get(WalletOperation, self.op_id)
            self.assertEqual(op.status, "completed")
            self.assertEqual(wallet.balance_usdt, Decimal("1.5"))

        async with async_session() as session:
            await wrq.complete_wallet_deposit(session, self.op_id)
            await session.commit()

        async with async_session() as session:
            wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == self.user_id))
            self.assertEqual(wallet.balance_usdt, Decimal("1.5"))
