from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, update, delete, and_, or_
from sqlalchemy.exc import SQLAlchemyError
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.types import Update, PreCheckoutQuery, Message, LabeledPrice
from aiogram.methods import CreateInvoiceLink
from aiogram.filters import CommandStart

from yookassa.domain.notification import WebhookNotification, WebhookNotificationFactory
from yookassa.domain.common import SecurityHelper

from models import init_db, async_session, UserStart, User, WalletOperation, WalletTransaction, UserTask, UserReward, ExchangeRate,Tariff, ServersVPN, Order, UserWallet, Payment, VPNSubscription
import requestsfile as rq
import buyextendrequests as berq
import yookassarequests as ykrq
import walletrequests as wrq
import tasksrequests as taskrq
import adminrequests as rqadm
from cryptopay_client import crypto
from scheduler import start_scheduler

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = "/webhook"

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ======================
# APP
# ======================
@asynccontextmanager
async def lifespan(app_: FastAPI):
    await init_db()
    start_scheduler()
    print("✅ VPN backend ready!")
    yield


app = FastAPI(title="ArtCry VPN", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],)


# TELEGRAM WEBHOOK
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


# Запуск бота (фиксация реф)===
@dp.message(CommandStart())
async def start_cmd(message: Message):
    referrer = None

    if message.text and len(message.text.split()) > 1:
        try:
            referrer = int(message.text.split()[1])
        except:
            pass

    async with async_session() as session:
        existing = await session.scalar(select(UserStart).where(UserStart.tg_id == message.from_user.id))

        if not existing:
            session.add(UserStart(tg_id=message.from_user.id, referrer_tg_id=referrer))
            await session.commit()

    await message.answer("👋 Добро пожаловать!\n\nОткройте mini app, чтобы продолжить.")


# REGISTER
class RegisterUser(BaseModel):
    tg_id: int
    tg_username: str | None = None
    referrer_tg_id: int | None = None


@app.post("/api/register")
async def register_user(data: RegisterUser):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if user:
            if data.tg_username and user.tg_username != data.tg_username:
                user.tg_username = data.tg_username
                await session.commit()
            return {"status": "exists", "idUser": user.idUser}

        start = await session.scalar(select(UserStart).where(UserStart.tg_id == data.tg_id)) # ищем referrer

        referrer_id = None
        if start and start.referrer_tg_id:
            ref_user = await session.scalar(select(User).where(User.tg_id == start.referrer_tg_id))
            if ref_user:
                referrer_id = ref_user.idUser

        user = User(tg_id=data.tg_id,tg_username=data.tg_username,userRole="user",referrer_id=referrer_id)
        session.add(user)
        await session.flush()
        session.add(UserWallet(idUser=user.idUser))

        if referrer_id:
            await rq.add_free_days(session, referrer_id, 1, "referral_signup", meta=f"referred_user:{user.idUser}")

        if start:
            await session.delete(start)

        await session.commit()
        return {"status": "ok", "idUser": user.idUser}


# баланс юзера
@app.get("/api/user/wallet/{tg_id}")
async def get_wallet(tg_id: int):
    wallet = await rq.get_user_wallet(tg_id)
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    return wallet


@app.get("/api/user/history/{tg_id}")
async def get_user_history(tg_id: int):
    return await rq.get_user_history(tg_id)


ORDER_ACTIVE_STATUSES = ("pending", "processing")
ORDER_TTL_MINUTES = 10

async def get_active_order_for_user(session, user_id: int):
    now = datetime.now(timezone.utc)

    q = (select(Order).where(Order.idUser == user_id,Order.status.in_(("pending", "processing"))).order_by(Order.created_at.desc()))

    order = await session.scalar(q)

    if not order:
        return None

    if order.expires_at and order.expires_at < now and order.status == "pending":
        order.status = "expired"
        await session.commit()
        return None

    return order



# проверка активного заказа
@app.get("/api/order/active/{tg_id}")
async def get_active_order(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return {"active": False}

        order = await get_active_order_for_user(session, user.idUser)
        if not order:
            return {"active": False}

        # ищем payment чтобы вернуть ссылку
        payment = await session.scalar(select(Payment).where(Payment.order_id == order.id).order_by(Payment.id.desc()))

        return {"active": True,
            "order": {"id": order.id,"status": order.status,"provider": order.provider,"purpose": order.purpose_order, "subscription_id": order.subscription_id,
                "created_at": order.created_at.isoformat(),"expires_at": order.expires_at.isoformat() if order.expires_at else None,
                "payment_id": payment.id if payment else None,"provider_payment_id": payment.provider_payment_id if payment else None,
                "payment_url": order.payment_url}
        }


@app.post("/api/order/cancel/{order_id}")
async def cancel_order(order_id: int):
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            raise HTTPException(404, "ORDER_NOT_FOUND")

        if order.status != "pending":
            raise HTTPException(400, "ORDER_CANT_CANCEL")

        order.status = "cancelled"
        await session.commit()

        return {"ok": True, "status": "cancelled"}



@app.get("/api/vpn/status/{tg_id}")
async def vpn_status(tg_id: int):
    active = await rq.has_active_subscription(tg_id)
    return {"active": active}


# =PUBLIC================
@app.get("/api/vpn/servers")
async def get_servers():
    return await rq.get_servers()


@app.get("/api/vpn/tariffs/{server_id}")
async def get_tariffs(server_id: int):
    try:
        return await rq.get_server_tariffs(server_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# подписки пользователя
@app.get("/api/vpn/my/{tg_id}")
async def my_vpns(tg_id: int):
    return await rq.get_my_vpns(tg_id)


# === КАСАЕМО ПОКУПКИ, ПРОДЛЕНИЯ И ОПЛАТ =====
@app.get("/api/payment/status/{payment_id}")
async def get_payment_status(payment_id: int):
    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            raise HTTPException(404, "Payment not found")

        return {"status": payment.status}

# Create Invoice
class CreateInvoiceRequest(BaseModel):
    tg_id: int
    tariff_id: int

@app.post("/api/vpn/create_invoice")
async def create_invoice(data: CreateInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        active = await get_active_order_for_user(session, user.idUser)
        if active:
            raise HTTPException(status_code=409,
                detail={"error": "ACTIVE_ORDER_EXISTS","order_id": active.id,
                    "status": active.status,"expires_at": active.expires_at.isoformat() if active.expires_at else None}
            )

        tariff = await session.scalar(select(Tariff).where(Tariff.idTarif == data.tariff_id))
        if not tariff or not tariff.is_active:
            raise HTTPException(status_code=404, detail="Tariff not found")

        server = await session.scalar(select(ServersVPN).where(ServersVPN.idServerVPN == tariff.server_id))
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        if not rate:
            raise HTTPException(status_code=400, detail="Exchange rate not set")

        price_usdt = Decimal(tariff.price_tarif)
        rate_usdt = Decimal(rate.rate)
        stars_price = int(price_usdt / rate_usdt)
        if stars_price < 1:
            stars_price = 1

        order = Order(idUser=user.idUser,server_id=server.idServerVPN,idTarif=tariff.idTarif,
            purpose_order="buy",amount=price_usdt,currency="USDT",provider="stars",status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ORDER_TTL_MINUTES)
        )
        session.add(order)
        await session.flush()
        await session.commit()
        
        invoice_link = await bot(
            CreateInvoiceLink(title=f"VPN {tariff.days} дней",description=server.nameVPN,payload=f"vpn:{order.id}",currency="XTR",
                prices=[LabeledPrice(label=f"{tariff.days} дней VPN", amount=stars_price)])
        )
        order.payment_url = invoice_link
        await session.commit()

        return {"invoice_link": invoice_link, "order_id": order.id}


# ПРОДЛЕНИЕ
class RenewInvoiceRequest(BaseModel):
    tg_id: int
    subscription_id: int
    tariff_id: int

@app.post("/api/vpn/renew-invoice")
async def renew_invoice(data: RenewInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(404, "User not found")

        sub = await session.get(VPNSubscription, data.subscription_id)
        if not sub:
            raise HTTPException(404, "VPN key not found")

        tariff = await session.get(Tariff, data.tariff_id)
        if not tariff or not tariff.is_active:
            raise HTTPException(404, "Subscription not found")

        server = await session.get(ServersVPN, sub.idServerVPN)

        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        if not rate:
            raise HTTPException(500, "Exchange rate not set")

        active = await get_active_order_for_user(session, user.idUser)
        if active:
            raise HTTPException(status_code=409,
                detail={"error": "ACTIVE_ORDER_EXISTS","order_id": active.id,"status": active.status,
                    "expires_at": active.expires_at.isoformat() if active.expires_at else None}
            )

        price_usdt = Decimal(tariff.price_tarif)
        stars_price = int(Decimal(tariff.price_tarif) / rate.rate)
        if stars_price < 1:
            stars_price = 1

        order = Order(idUser=user.idUser,server_id=server.idServerVPN,idTarif=tariff.idTarif,subscription_id=sub.id,
            purpose_order="extension",amount=price_usdt,currency="USDT",provider="stars",status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ORDER_TTL_MINUTES)
        )

        session.add(order)
        await session.flush()
        await session.commit()

        invoice_link = await bot(
            CreateInvoiceLink(title=f"Продление VPN {tariff.days} дней",description=server.nameVPN,
                payload=f"renew:{order.id}",currency="XTR",
                prices=[LabeledPrice(label=f"{tariff.days} дней VPN", amount=stars_price)])
        )
        order.payment_url = invoice_link
        await session.commit()
        return {"invoice_link": invoice_link, "order_id": order.id}


# ПОПОЛНЕНИЕ
class WalletDepositRequest(BaseModel):
    tg_id: int
    amount_usdt: Decimal


@app.post("/api/wallet/deposit/stars")
async def wallet_deposit_stars(data: WalletDepositRequest):
    result = await wrq.create_stars_deposit(data.tg_id,Decimal(data.amount_usdt))

    invoice_link = await bot(CreateInvoiceLink(title="Пополнение баланса",description=f"Баланс +${data.amount_usdt}",
            payload=f"wallet:{result['wallet_operation_id']}",currency="XTR",
            prices=[LabeledPrice(label="Пополнение баланса", amount=result["stars_amount"])])
    )
    return {"invoice_link": invoice_link,"order_id": result["wallet_operation_id"],"stars": result["stars_amount"]}

@app.post("/api/wallet/deposit/yookassa")
async def wallet_deposit_yookassa(data: WalletDepositRequest):
    async with async_session() as session:
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "RUB_USDT"))
        if not rate:
            raise HTTPException(500, "RUB rate not set")

    result = await wrq.create_yookassa_deposit(data.tg_id,Decimal(data.amount_usdt))
    amount_rub = Decimal(data.amount_usdt) * Decimal(rate.rate)

    payment_id, confirmation_url = await ykrq.create_yookassa_payment(
        result["wallet_operation_id"],
        amount_rub,
        f"Wallet top-up {data.amount_usdt} USDT",
        metadata={"purpose": "wallet"}
    )

    async with async_session() as session:
        session.add(Payment(wallet_operation_id=result["wallet_operation_id"],provider="yookassa",
            provider_payment_id=payment_id,status="pending"))
        await session.commit()

    return {"confirmation_url": confirmation_url,"order_id": result["wallet_operation_id"],"amount_rub": str(amount_rub)}



# --- Создание заказа по Stars --- 
class OrderRequest(BaseModel):
    tg_id: int
    server_id: int
    tariff_id: int

@app.post("/api/vpn/order")
async def create_order_endpoint(data: OrderRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        tariff = await session.get(Tariff, data.tariff_id)
        if not tariff or not tariff.is_active:
            raise HTTPException(status_code=404, detail="Tariff not found")

        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        if not rate:
            raise HTTPException(status_code=500, detail="Exchange rate not found")

        amount_stars = int(tariff.price_tarif / rate.rate)
        return await berq.create_order(user.idUser, data.server_id, data.tariff_id, Decimal(amount_stars), currency="XTR")


# TELEGRAM HANDLERS
@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload

    try:
        prefix, entity_id = payload.split(":")
        entity_id = int(entity_id)
    except Exception:
        return

    provider_payment_id = message.successful_payment.telegram_payment_charge_id

    # ПОПОЛНЕНИЕ БАЛАНСА
    if prefix == "wallet":
        async with async_session() as session:
            op = await session.get(WalletOperation, entity_id)
            if not op:
                logger.warning("Stars wallet op not found: %s", entity_id)
                return
            if op.status != "pending":
                logger.info("Stars wallet op already handled: %s status=%s", op.id, op.status)
                return

            payment = Payment(wallet_operation_id=op.id,provider="telegram_stars",
                provider_payment_id=provider_payment_id,status="paid")
            session.add(payment)

            await wrq.complete_wallet_deposit(session, op.id)
            await session.commit()

        logger.info("Stars wallet completed: op_id=%s payment_id=%s", entity_id, provider_payment_id)
        await message.answer("✅ Баланс успешно пополнен!")
        return

    # ПОКУПКА/ПРОДЛЕНИЕ
    if prefix not in ("vpn", "renew"):
        return

    async with async_session() as session:
        order = await session.get(Order, entity_id)
        if not order:
            logger.warning("Stars order not found: %s", entity_id)
            return
        if order.status != "pending":
            logger.info("Stars order already handled: %s status=%s", order.id, order.status)
            return

        order.status = "paid"
        payment = Payment(order_id=order.id,provider="telegram_stars",provider_payment_id=provider_payment_id,status="paid")
        session.add(payment)
        await session.flush()
        order.status = "processing"

        tariff = await session.get(Tariff, order.idTarif)
        server = await session.get(ServersVPN, order.server_id)

        try:
            if order.purpose_order == "buy":
                vpn_data = await berq.create_vpn_xui(order.idUser,order.server_id,tariff.days)

            elif order.purpose_order == "extension":
                vpn_data = await berq.pay_and_extend_vpn(subscription_id=order.subscription_id,tariff_id=order.idTarif)
            else:
                raise Exception("Unknown order purpose")

        except Exception as e:
            order.status = "failed"
            await session.commit()
            logger.exception("Stars order failed: %s error=%s", order.id, e)
            await message.answer(f"❌ Ошибка создания VPN: {e}")
            return

        order.status = "completed"
        await rq.process_referral_reward(session, order)
        await session.commit()
        logger.info("Stars order completed: %s payment_id=%s", order.id, provider_payment_id)

        if order.purpose_order == "buy":
            await message.answer(
                f"✅ <b>VPN готов!</b>\n"
                f"Сервер: {server.nameVPN}\n"
                f"Действует до: {vpn_data['expires_at_human']}\n\n"
                f"<b>Ваш ключ:</b>\n"
                f"<code>{vpn_data['access_data']}</code>",parse_mode="HTML"
            )

        elif order.purpose_order == "extension":
            await message.answer(
                f"♻️ <b>VPN успешно продлён!</b>\n"
                f"➕ Добавлено дней: {vpn_data['days_added']}\n"
                f"🕒 Новый срок: {vpn_data['expires_at_human']}",parse_mode="HTML"
            )


# ===== КРИПТА x Cryptobot
class CryptoInvoiceRequest(BaseModel):
    tg_id: int
    tariff_id: int

@app.post("/api/vpn/crypto-invoice")
async def create_crypto_invoice(data: CryptoInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        tariff = await session.get(Tariff, data.tariff_id)
        active = await get_active_order_for_user(session, user.idUser)
        if active:
            raise HTTPException(status_code=409,
                detail={"error": "ACTIVE_ORDER_EXISTS","order_id": active.id,"status": active.status,
                    "expires_at": active.expires_at.isoformat() if active.expires_at else None}
            )

        if not user or not tariff or not tariff.is_active:
            raise HTTPException(404, "Invalid user or tariff")

        order = Order(idUser=user.idUser,server_id=tariff.server_id,idTarif=tariff.idTarif,purpose_order="buy",
            amount=Decimal(tariff.price_tarif),currency="USDT",provider="cryptobot",status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ORDER_TTL_MINUTES))
        session.add(order)
        await session.flush()

        # инвойс CryptoBot
        invoice = await crypto.create_invoice(asset="USDT",amount=float(tariff.price_tarif),payload=f"buy:{order.id}")
        order.payment_url = invoice.mini_app_invoice_url

        payment = Payment(order_id=order.id,provider="cryptobot",provider_payment_id=str(invoice.invoice_id),status="pending")
        session.add(payment)
        await session.commit()

        return {"invoice_url": invoice.mini_app_invoice_url, "order_id": order.id}


# ПРОДЛЕНИЕ cryptobot
class RenewCryptoInvoiceRequest(BaseModel):
    tg_id: int
    subscription_id: int
    tariff_id: int

@app.post("/api/vpn/renew-crypto-invoice")
async def renew_crypto_invoice(data: RenewCryptoInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(404, "User not found")

        sub = await session.get(VPNSubscription, data.subscription_id)
        if not sub:
            raise HTTPException(404, "Subscription not found")

        tariff = await session.get(Tariff, data.tariff_id)
        if not tariff or not tariff.is_active:
            raise HTTPException(404, "Tariff not found")
        
        active = await get_active_order_for_user(session, user.idUser)
        if active:
            raise HTTPException(status_code=409,
                detail={"error": "ACTIVE_ORDER_EXISTS","order_id": active.id,"status": active.status,
                    "expires_at": active.expires_at.isoformat() if active.expires_at else None}
            )

        order = Order(idUser=user.idUser,server_id=sub.idServerVPN,idTarif=tariff.idTarif,subscription_id=sub.id,
            purpose_order="extension",amount=Decimal(tariff.price_tarif),currency="USDT",provider="cryptobot",status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ORDER_TTL_MINUTES))
        session.add(order)
        await session.flush()

        invoice = await crypto.create_invoice(asset="USDT",amount=float(tariff.price_tarif),payload=f"renew:{order.id}")
        order.payment_url = invoice.mini_app_invoice_url
        
        payment = Payment(order_id=order.id,provider="cryptobot",provider_payment_id=str(invoice.invoice_id),status="pending")
        session.add(payment)
        await session.commit()

        return {"invoice_url": invoice.mini_app_invoice_url,"order_id": order.id}


# ПОПОПЛЛНЕНИЕ cryptobot
@app.post("/api/wallet/deposit/crypto")
async def wallet_deposit_crypto(data: WalletDepositRequest):
    result = await wrq.create_crypto_deposit(data.tg_id,Decimal(data.amount_usdt))

    invoice = await crypto.create_invoice(asset="USDT",amount=float(data.amount_usdt),payload=f"wallet:{result['wallet_operation_id']}")

    async with async_session() as session:
        payment = Payment(wallet_operation_id=result["wallet_operation_id"],provider="cryptobot",
            provider_payment_id=str(invoice.invoice_id),status="pending")
        session.add(payment)
        await session.commit()

    return {"invoice_url": invoice.mini_app_invoice_url,"order_id": result["wallet_operation_id"]}


# Webhoock от Cryptobot
@app.post("/api/crypto/webhook")
async def crypto_webhook(data: dict):
    if data.get("update_type") != "invoice_paid":
        return {"ok": True}

    payload = data.get("payload", {})
    invoice_id = str(payload.get("invoice_id"))
    raw_payload = payload.get("payload")

    if not invoice_id or not raw_payload:
        return {"ok": True}

    try:
        prefix, entity_id = raw_payload.split(":")
        entity_id = int(entity_id)
    except:
        return {"ok": True}

    async with async_session() as session:
        payment = await session.scalar(select(Payment).where(Payment.provider == "cryptobot",Payment.provider_payment_id == invoice_id))
        if not payment:
            logger.warning("Cryptobot payment not found: %s", invoice_id)
            return {"ok": True}
        if payment.status == "paid":
            logger.info("Cryptobot payment already handled: %s", invoice_id)
            return {"ok": True}

        payment.status = "paid"

        # ПОПОЛНЕНИЕ БАЛАНСА
        if prefix == "wallet":
            op = await session.get(WalletOperation, entity_id)
            if not op:
                logger.warning("Cryptobot wallet op not found: %s", entity_id)
                return {"ok": True}
            if op.status != "pending":
                logger.info("Cryptobot wallet op already handled: %s status=%s", op.id, op.status)
                return {"ok": True}
            
            user = await session.get(User, op.idUser)
            await wrq.complete_wallet_deposit(session, op.id)
            await session.commit()
            logger.info("Cryptobot wallet completed: op_id=%s payment_id=%s", op.id, invoice_id)
            await bot.send_message(chat_id=user.tg_id,text=("✅ Баланс успешно пополнен!"))
            
            return {"ok": True}
        
        # ПРОДЛЕНИЕ VPN
        if prefix == "renew":
            order = await session.get(Order, entity_id)
            if not order:
                logger.warning("Cryptobot order not found: %s", entity_id)
                return {"ok": True}
            if order.status != "pending":
                logger.info("Cryptobot order already handled: %s status=%s", order.id, order.status)
                return {"ok": True}

            order.status = "processing"
            tariff = await session.get(Tariff, order.idTarif)
            server = await session.get(ServersVPN, order.server_id)
            user = await session.get(User, order.idUser)

            try:
                vpn_data = await berq.pay_and_extend_vpn(subscription_id=order.subscription_id,tariff_id=order.idTarif)
            except Exception:
                order.status = "failed"
                await session.commit()
                logger.exception("Cryptobot renew failed: %s", order.id)
                return {"ok": True}

            order.status = "completed"
            await rq.process_referral_reward(session, order)
            await session.commit()
            logger.info("Cryptobot renew completed: %s payment_id=%s", order.id, invoice_id)

            await bot.send_message(chat_id=user.tg_id,
                text=(
                    f"♻️ <b>VPN успешно продлён!</b>\n"
                    f"➕ Добавлено дней: {vpn_data['days_added']}\n"
                    f"🕒 Новый срок: {vpn_data['expires_at_human']}"
                ),parse_mode="HTML"
            )

            return {"ok": True}

        # ПОКУПКА VPN
        if prefix == "buy":
            order = await session.get(Order, entity_id)
            if not order:
                logger.warning("Cryptobot order not found: %s", entity_id)
                return {"ok": True}
            if order.status != "pending":
                logger.info("Cryptobot order already handled: %s status=%s", order.id, order.status)
                return {"ok": True}

            order.status = "processing"

            tariff = await session.get(Tariff, order.idTarif)
            server = await session.get(ServersVPN, order.server_id)
            user = await session.get(User, order.idUser)

            try:
                vpn_data = await berq.create_vpn_xui(order.idUser, order.server_id, tariff.days)
            except Exception:
                order.status = "failed"
                await session.commit()
                logger.exception("Cryptobot buy failed: %s", order.id)
                return {"ok": True}

            order.status = "completed"
            await rq.process_referral_reward(session, order)
            await session.commit()
            logger.info("Cryptobot buy completed: %s payment_id=%s", order.id, invoice_id)

            await bot.send_message(chat_id=user.tg_id,
                text=(
                    f"✅ <b>VPN готов!</b>\n"
                    f"Сервер: {server.nameVPN}\n"
                    f"Действует до: {vpn_data['expires_at_human']}\n\n"
                    f"<b>Ваш ключ:</b>\n"
                    f"<code>{vpn_data['access_data']}</code>"
                ),parse_mode="HTML"
            )

    return {"ok": True}



# ЮKASSA покупка 
class YooKassaInvoiceRequest(BaseModel):
    tg_id: int
    tariff_id: int

@app.post("/api/vpn/yookassa-invoice")
async def create_yookassa_invoice(data: YooKassaInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        tariff = await session.get(Tariff, data.tariff_id)

        if not user or not tariff or not tariff.is_active:
            raise HTTPException(404, "Invalid user or tariff")

        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "RUB_USDT"))
        if not rate:
            raise HTTPException(500, "RUB rate not set")
        
        active = await get_active_order_for_user(session, user.idUser)
        if active:
            raise HTTPException(status_code=409,
                detail={"error": "ACTIVE_ORDER_EXISTS","order_id": active.id,"status": active.status,
                    "expires_at": active.expires_at.isoformat() if active.expires_at else None}
            )

        price_rub = Decimal(tariff.price_tarif) * Decimal(rate.rate)

        order = Order(idUser=user.idUser,server_id=tariff.server_id,idTarif=tariff.idTarif,purpose_order="buy",
            amount=Decimal(tariff.price_tarif),currency="USDT",provider="yookassa",status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ORDER_TTL_MINUTES))
        session.add(order)
        await session.flush()

        payment_id, confirmation_url = await ykrq.create_yookassa_payment(order.id,price_rub,f"Buy VPN {tariff.days} дней, idUser: {user.idUser}")
        order.payment_url = confirmation_url

        payment = Payment(order_id=order.id,provider="yookassa",provider_payment_id=payment_id,status="pending")
        session.add(payment)
        await session.commit()
        print(f"🧾 YooKassa invoice requested: tg_id={data.tg_id}, tariff_id={data.tariff_id}")

        return {"confirmation_url": confirmation_url,"order_id": order.id,"amount_rub": str(price_rub)}


# ЮKASSA продление
class RenewYooKassaInvoiceRequest(BaseModel):
    tg_id: int
    subscription_id: int
    tariff_id: int

@app.post("/api/vpn/renew-yookassa-invoice")
async def renew_yookassa_invoice(data: RenewYooKassaInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        sub = await session.get(VPNSubscription, data.subscription_id)
        tariff = await session.get(Tariff, data.tariff_id)

        if not user or not sub or not tariff or not tariff.is_active:
            raise HTTPException(404, "Invalid data")

        active = await get_active_order_for_user(session, user.idUser)
        if active:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "ACTIVE_ORDER_EXISTS",
                    "order_id": active.id,
                    "status": active.status,
                    "expires_at": active.expires_at.isoformat() if active.expires_at else None
                }
            )

        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "RUB_USDT"))
        if not rate:
            raise HTTPException(500, "RUB rate not set")

        price_rub = Decimal(tariff.price_tarif) * Decimal(rate.rate)

        order = Order(idUser=user.idUser,server_id=sub.idServerVPN,idTarif=tariff.idTarif,
            subscription_id=sub.id,
            purpose_order="extension",
            amount=Decimal(tariff.price_tarif),
            currency="USDT",
            provider="yookassa",
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ORDER_TTL_MINUTES)
        )
        session.add(order)
        await session.flush()

        payment_id, confirmation_url = await ykrq.create_yookassa_payment(order.id,price_rub,f"Renew VPN {tariff.days} days")

        order.payment_url = confirmation_url
        session.add(Payment(order_id=order.id,provider="yookassa",provider_payment_id=payment_id,status="pending"))

        await session.commit()

        return {"confirmation_url": confirmation_url,"order_id": order.id,"amount_rub": str(price_rub)}



# юkassa webhoock

@app.post("/api/yookassa/webhook")
async def yookassa_webhook(request: Request):
    data = await request.json()
    notification = WebhookNotificationFactory().create(data)

    if notification.event != "payment.succeeded":
        return {"ok": True}

    payment_obj = notification.object
    metadata = payment_obj.metadata or {}
    purpose = metadata.get("purpose")
    try:
        order_id = int(metadata.get("order_id"))
    except (TypeError, ValueError):
        return {"ok": True}

    async with async_session() as session:
        if purpose == "wallet":
            op = await session.get(WalletOperation, order_id)
            if not op:
                logger.warning("YooKassa wallet op not found: %s", order_id)
                return {"ok": True}
            if op.status != "pending":
                logger.info("YooKassa wallet op already handled: %s status=%s", op.id, op.status)
                return {"ok": True}

            payment = await session.scalar(select(Payment).where(Payment.provider == "yookassa").where(Payment.provider_payment_id == payment_obj.id))
            if payment:
                payment.status = "paid"

            user = await session.get(User, op.idUser)
            await wrq.complete_wallet_deposit(session, op.id)
            await session.commit()

            logger.info("YooKassa wallet completed: op_id=%s payment_id=%s", op.id, payment_obj.id)
            await bot.send_message(chat_id=user.tg_id,text=("✅ Баланс успешно пополнен!"))
            return {"ok": True}

        order = await session.get(Order, order_id)
        if not order:
            logger.warning("YooKassa order not found: %s", order_id)
            return {"ok": True}
        if order.status != "pending":
            logger.info("YooKassa order already handled: %s status=%s", order.id, order.status)
            return {"ok": True}

        order.status = "processing"

        payment = await session.scalar(select(Payment).where(Payment.provider == "yookassa").where(Payment.provider_payment_id == payment_obj.id))
        if payment:
            payment.status = "paid"

        user = await session.get(User, order.idUser)
        tariff = await session.get(Tariff, order.idTarif)
        server = await session.get(ServersVPN, order.server_id)

        try:
            if order.purpose_order == "buy":
                vpn_data = await berq.create_vpn_xui(user_id=user.idUser,server_id=order.server_id,tariff_days=tariff.days)

                order.subscription_id = vpn_data["subscription_id"]

                await bot.send_message(chat_id=user.tg_id,
                    text=(
                        f"✅ <b>VPN готов!</b>\n"
                        f"Сервер: {server.nameVPN}\n"
                        f"Действует до: {vpn_data['expires_at_human']}\n\n"
                        f"<b>Ваш ключ:</b>\n"
                        f"<code>{vpn_data['access_data']}</code>"
                    ),parse_mode="HTML"
                )

            elif order.purpose_order == "extension":
                vpn_data = await berq.pay_and_extend_vpn(subscription_id=order.subscription_id,tariff_id=order.idTarif)

                await bot.send_message(chat_id=user.tg_id,
                    text=(
                        f"♻️ <b>VPN успешно продлён!</b>\n"
                        f"➕ Добавлено дней: {vpn_data['days_added']}\n"
                        f"🕒 Новый срок: {vpn_data['expires_at_human']}"
                    ),parse_mode="HTML"
                )

            order.status = "completed"
            await rq.process_referral_reward(session, order)
            await session.commit()
            logger.info("YooKassa order completed: %s payment_id=%s", order.id, payment_obj.id)
            return {"ok": True}

        except Exception as e:
            order.status = "failed"
            await session.commit()
            logger.exception("YooKassa webhook error: %s order=%s", e, order.id)
            return {"ok": True}




# buy С БАЛАНСА
class BuyFromBalanceRequest(BaseModel):
    tg_id: int
    tariff_id: int

@app.post("/api/vpn/buy-from-balance")
async def buy_from_balance(data: BuyFromBalanceRequest):
    try:
        result = await berq.buy_vpn_from_balance(tg_id=data.tg_id,tariff_id=data.tariff_id)
        await bot.send_message(chat_id=data.tg_id,
            text=(
                f"✅ <b>VPN готов!</b>\n"
                f"Сервер: {result['server_name']}\n"
                f"Действует до: {result['expires_at_human']}\n\n"
                f"<b>Ваш ключ:</b>\n"
                f"<code>{result['access_data']}</code>"
            ),parse_mode="HTML"
        )
        return result

    except Exception as e:
        if str(e) == "NOT_ENOUGH_BALANCE":
            raise HTTPException(status_code=400, detail="NOT_ENOUGH_BALANCE")
        raise HTTPException(status_code=500, detail=str(e))


# продление С БАЛАНСА
class RenewFromBalanceRequest(BaseModel):
    tg_id: int
    subscription_id: int
    tariff_id: int

@app.post("/api/vpn/renew-from-balance")
async def renew_from_balance(data: RenewFromBalanceRequest):
    try:
        result = await berq.extend_vpn_from_balance(tg_id=data.tg_id,subscription_id=data.subscription_id,tariff_id=data.tariff_id)
        await bot.send_message(chat_id=data.tg_id,
            text=(
                f"♻️ <b>VPN успешно продлён!</b>\n"
                f"➕ Добавлено дней: {result['days_added']}\n"
                f"🕒 Новый срок: {result['expires_at_human']}"
            ),parse_mode="HTML"
        )
        return result
    
    except Exception as e:
        if str(e) == "NOT_ENOUGH_BALANCE":
            raise HTTPException(400, "NOT_ENOUGH_BALANCE")
        if str(e) == "ACTIVE_ORDER_EXISTS":
            raise HTTPException(409, "ACTIVE_ORDER_EXISTS")
        raise HTTPException(400, str(e))





# Проверка успешного заказа
@app.get("/api/order/status/{order_id}")
async def get_order_status(order_id: int):
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            raise HTTPException(404, "Order not found")
        return {"status": order.status}

# Проверка успешной оплаты пополнения
@app.get("/api/wallet/status/{operation_id}")
async def get_wallet_operation_status(operation_id: int):
    async with async_session() as session:
        op = await session.get(WalletOperation, operation_id)
        if not op:
            raise HTTPException(404, "Wallet operation not found")
        return {"status": op.status}


@app.get("/api/rate/xtr")
async def get_xtr_rate():
    async with async_session() as session:
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        if not rate:
            raise HTTPException(404, "Rate not set")
        return {"rate": str(rate.rate)}


@app.get("/api/rate/rub")
async def get_rub_rate():
    async with async_session() as session:
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "RUB_USDT"))
        return {"rate": str(rate.rate)}


# ======================
# REFERRALS
@app.get("/api/referrals/{tg_id}")
async def referrals_list(tg_id: int):
    return await rq.get_referrals_list(tg_id)

@app.get("/api/admin/referrals-count/{tg_id}")
async def get_referrals_count(
    tg_id: int = Path(..., description="TG ID пользователя")):
    count = await rq.get_referrals_count(tg_id)
    return {"count": count}

@app.get("/api/admin/referrals/{tg_id}")
async def get_referrals(
    tg_id: int = Path(..., description="TG ID пользователя")):
    return await rq.get_referrals_list(tg_id)


# ======================
# TASKS x Rewards
@app.get("/api/tasks/{tg_id}")
async def get_tasks(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        completed = await session.scalars(select(UserTask.task_key).where(UserTask.idUser == user.idUser))
        completed_keys = set(completed)

        return [{**task,"completed": task["key"] in completed_keys}
            for task in taskrq.TASKS
        ]


@app.post("/api/tasks/check/{task_key}")
async def check_task(task_key: str, tg_id: int):
    task = next(t for t in taskrq.TASKS if t["key"] == task_key)

    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))

    return await taskrq.check_and_complete_task(user, task)


@app.get("/api/rewards/{tg_id}")
async def get_rewards(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        rewards = await session.scalars(select(UserReward)
            .where(UserReward.idUser == user.idUser,UserReward.is_activated == False)
        )

        return [{"id": r.id,"days": r.days
        } for r in rewards]


@app.get("/api/rewards/preview")
async def reward_preview(tg_id: int, reward_id: int, server_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise HTTPException(404, "User not found")

        reward = await session.get(UserReward, reward_id)
        if not reward or reward.idUser != user.idUser:
            raise HTTPException(404, "Reward not found")

        sub = await session.scalar(select(VPNSubscription).where(VPNSubscription.idUser == user.idUser,VPNSubscription.idServerVPN == server_id))

        return {"mode": "extend" if sub else "create","days": reward.days}


@app.post("/api/rewards/activate")
async def activate_reward_api(tg_id: int, reward_id: int, server_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise HTTPException(404, "User not found")

    await taskrq.activate_reward(user.idUser, reward_id, server_id)

    return {"status": "ok"}


@app.get("/api/free-days/{tg_id}")
async def get_free_days(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise HTTPException(404, "User not found")
    return await taskrq.get_free_days_data(user.idUser)


@app.post("/api/checkin")
async def checkin(tg_id: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise HTTPException(404, "User not found")
    return await taskrq.perform_checkin(user.idUser)


@app.post("/api/checkin/exchange")
async def exchange_checkins(tg_id: int, checkins: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise HTTPException(404, "User not found")
    return await taskrq.exchange_checkins(user.idUser, checkins)


@app.post("/api/free-days/activate")
async def activate_free_days(tg_id: int, server_id: int, days: int):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            raise HTTPException(404, "User not found")
    return await taskrq.activate_free_days(user.idUser, server_id, days)









# =========================================================================================================================================
# ADMIN MODELS
# ======================

# ======================
# ADMIN: USERS
# ======================
class AdminUserCreate(BaseModel):
    tg_id: int
    tg_username: str | None = None
    userRole: str
    referrer_id: int | None = None

class AdminUserUpdate(BaseModel):
    tg_id: int
    tg_username: str | None = None
    userRole: str
    referrer_id: int | None = None


@app.get("/api/admin/users")
async def admin_get_users():
    return await rqadm.admin_get_users()

@app.post("/api/admin/users")
async def admin_add_user(data: AdminUserCreate):
    return await rqadm.admin_add_user(**data.dict())

@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, data: AdminUserUpdate):
    return await rqadm.admin_update_user(user_id, data.dict(exclude_unset=True))

@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int):
    return await rqadm.admin_delete_user(user_id)


# ======================
# ADMIN: WALLETS
# ======================
class WalletCreate(BaseModel):
    idUser: int
    balance_usdt: Decimal = Decimal("0.0")

class WalletUpdate(BaseModel):
    idUser: int
    balance_usdt: Decimal


@app.get("/api/admin/wallets")
async def admin_get_wallets():
    return await rqadm.admin_get_wallets()

@app.post("/api/admin/wallets")
async def admin_add_wallet(data: WalletCreate):
    return await rqadm.admin_add_wallet(data.dict())

@app.put("/api/admin/wallets/{wallet_id}")
async def admin_update_wallet(wallet_id: int, data: WalletUpdate):
    return await rqadm.admin_update_wallet(wallet_id, data.dict())

@app.delete("/api/admin/wallets/{wallet_id}")
async def admin_delete_wallet(wallet_id: int):
    return await rqadm.admin_delete_wallet(wallet_id)


# ======================
# ADMIN: WALLET TRANSACTIONS
# ======================
class WalletTransactionCreate(BaseModel):
    wallet_id: int
    amount: Decimal
    type: str
    description: str | None = None

class WalletTransactionUpdate(BaseModel):
    wallet_id: int
    amount: Decimal
    type: str
    description: str | None = None


@app.get("/api/admin/wallet-transactions")
async def admin_get_wallet_transactions():
    return await rqadm.admin_get_wallet_transactions()

@app.post("/api/admin/wallet-transactions")
async def admin_add_wallet_transaction(data: WalletTransactionCreate):
    return await rqadm.admin_add_wallet_transaction(data.dict())

@app.put("/api/admin/wallet-transactions/{tx_id}")
async def admin_update_wallet_transaction(tx_id: int, data: WalletTransactionUpdate):
    return await rqadm.admin_update_wallet_transaction(tx_id, data.dict())

@app.delete("/api/admin/wallet-transactions/{tx_id}")
async def admin_delete_wallet_transaction(tx_id: int):
    return await rqadm.admin_delete_wallet_transaction(tx_id)


# ======================
# ADMIN: TYPES
# ======================
class TypeVPNCreate(BaseModel):
    nameType: str
    descriptionType: str
    
@app.get("/api/admin/types")
async def admin_get_types():
    return await rqadm.admin_get_types()

@app.post("/api/admin/types")
async def admin_add_type(data: TypeVPNCreate):
    return await rqadm.admin_add_type(data.nameType, data.descriptionType)

@app.patch("/api/admin/types/{type_id}")
async def admin_update_type(type_id: int, data: TypeVPNCreate):
    return await rqadm.admin_update_type(type_id, data.nameType, data.descriptionType)

@app.delete("/api/admin/types/{type_id}")
async def admin_delete_type(type_id: int):
    return await rqadm.admin_delete_type(type_id)


# ======================
# ADMIN: COUNTRIES
# ======================
class CountryCreate(BaseModel):
    nameCountry: str

@app.get("/api/admin/countries")
async def admin_get_countries():
    return await rqadm.admin_get_countries()

@app.post("/api/admin/countries")
async def admin_add_country(data: CountryCreate):
    return await rqadm.admin_add_country(data.nameCountry)

@app.patch("/api/admin/countries/{country_id}")
async def admin_update_country(country_id: int, data: CountryCreate):
    return await rqadm.admin_update_country(country_id, data.nameCountry)

@app.delete("/api/admin/countries/{country_id}")
async def admin_delete_country(country_id: int):
    return await rqadm.admin_delete_country(country_id)


# ======================
# ADMIN: SERVERS
# ======================
class ServerCreate(BaseModel):
    nameVPN: str
    price_usdt: Decimal
    max_conn: int
    server_ip: str
    api_url: str
    api_token: str
    xui_username: str
    xui_password: str
    inbound_port: int
    idTypeVPN: int
    idCountry: int
    is_active: bool

class ServerUpdate(ServerCreate):
    pass

@app.get("/api/admin/servers")
async def admin_get_servers():
    return await rqadm.admin_get_servers()

@app.post("/api/admin/servers")
async def admin_add_server(server: ServerCreate):
    return await rqadm.admin_add_server(server)

@app.patch("/api/admin/servers/{server_id}")
async def admin_update_server(server_id: int, data: ServerCreate):
    return await rqadm.admin_update_server(server_id, data.dict())

@app.delete("/api/admin/servers/{server_id}")
async def admin_delete_server(server_id: int):
    return await rqadm.admin_delete_server(server_id)

@app.get("/api/vpn/servers-full")
async def get_servers_full():
    return await rq.get_servers_full()


# ======================
# ADMIN: TARIFF
# ======================
class TariffCreate(BaseModel):
    server_id: int
    days: int
    price_tarif: Decimal
    is_active: bool = True

class TariffUpdate(TariffCreate):
    pass


@app.get("/api/admin/tariffs/{server_id}")
async def admin_get_tariffs(server_id: int):
    return await rqadm.admin_get_tariffs(server_id)

@app.post("/api/admin/tariffs")
async def admin_add_tariff(data: TariffCreate):
    return await rqadm.admin_add_tariff(data.server_id, data.days, data.price_tarif, data.is_active)

@app.patch("/api/admin/tariffs/{tariff_id}")
async def admin_update_tariff(tariff_id: int, data: TariffCreate):
    return await rqadm.admin_update_tariff(tariff_id, data.days, data.price_tarif, data.is_active)

@app.delete("/api/admin/tariffs/{tariff_id}")
async def admin_delete_tariff(tariff_id: int):
    return await rqadm.admin_delete_tariff(tariff_id)


# ======================
# ADMIN: ExchangeRate
# ======================
class ExchangeRateUpdate(BaseModel):
    rate: Decimal


@app.get("/api/admin/exchange-rate/{pair}")
async def admin_get_exchange_rate(pair: str):
    rate = await rqadm.admin_get_exchange_rate(pair)
    if not rate:
        raise HTTPException(status_code=404, detail="Rate not found")
    return rate

@app.put("/api/admin/exchange-rate/{pair}")
async def admin_set_exchange_rate(pair: str, data: ExchangeRateUpdate):
    return await rqadm.admin_set_exchange_rate(pair, data.rate)



# ======================
# ADMIN: Order
# ======================
class OrderCreate(BaseModel):
    idUser: int
    server_id: int
    idTarif: int
    purpose_order: str
    amount: int
    currency: str
    status: str = "pending"

class OrderUpdate(BaseModel):
    idUser: int
    server_id: int
    idTarif: int
    purpose_order: str
    amount: int
    currency: str
    status: str


@app.get("/api/admin/orders")
async def admin_get_orders():
    return await rqadm.admin_get_orders()

@app.post("/api/admin/orders")
async def admin_add_order(data: OrderCreate):
    return await rqadm.admin_add_order(data.dict())

@app.put("/api/admin/orders/{order_id}")
async def admin_update_order(order_id: int, data: OrderUpdate):
    return await rqadm.admin_update_order(order_id, data.dict())

@app.delete("/api/admin/orders/{order_id}")
async def admin_delete_order(order_id: int):
    return await rqadm.admin_delete_order(order_id)

@app.get("/api/admin/tariffs")
async def admin_get_all_tariffs():
    return await rqadm.admin_get_all_tariffs()



# ======================
# ADMIN: Paynent
# ======================
class PaymentCreate(BaseModel):
    order_id: int
    provider: str
    provider_payment_id: str
    status: str

class PaymentUpdate(BaseModel):
    order_id: int
    provider: str
    provider_payment_id: str
    status: str


@app.get("/api/admin/payments")
async def admin_get_payments():
    return await rqadm.admin_get_payments()

@app.post("/api/admin/payments")
async def admin_add_payment(data: PaymentCreate):
    return await rqadm.admin_add_payment(data.dict())

@app.put("/api/admin/payments/{payment_id}")
async def admin_update_payment(payment_id: int, data: PaymentUpdate):
    return await rqadm.admin_update_payment(payment_id, data.dict())

@app.delete("/api/admin/payments/{payment_id}")
async def admin_delete_payment(payment_id: int):
    return await rqadm.admin_delete_payment(payment_id)


# ======================
# ADMIN: VPN SUBSCRIPTIONS
# ======================
class VPNSubscriptionCreate(BaseModel):
    idUser: int
    idServerVPN: int
    provider: str
    provider_client_email: str
    provider_client_uuid: str
    access_data: str
    expires_at: datetime
    is_active: bool = True
    status: str = "active"


class VPNSubscriptionUpdate(BaseModel):
    expires_at: datetime | None = None
    is_active: bool | None = None
    status: str | None = None
    provider_client_email: str | None = None
    provider_client_uuid: str | None = None
    access_data: str | None = None


@app.get("/api/admin/vpn-subscriptions")
async def admin_get_vpn_subscriptions():
    return await rqadm.admin_get_vpn_subscriptions()

@app.post("/api/admin/vpn-subscriptions")
async def admin_add_vpn_subscription(data: VPNSubscriptionCreate):
    return await rqadm.admin_add_vpn_subscription(data.dict())

@app.put("/api/admin/vpn-subscriptions/{sub_id}")
async def admin_update_vpn_subscription(sub_id: int, data: VPNSubscriptionUpdate):
    return await rqadm.admin_update_vpn_subscription(sub_id, data.dict())

@app.delete("/api/admin/vpn-subscriptions/{sub_id}")
async def admin_delete_vpn_subscription(sub_id: int):
    return await rqadm.admin_delete_vpn_subscription(sub_id)


# ======================
# ADMIN: ReferralConfig
# ======================
class ReferralConfigCreate(BaseModel):
    percent: int
    is_active: bool = True

class ReferralConfigUpdate(BaseModel):
    percent: int
    is_active: bool


@app.get("/api/admin/referral-config")
async def admin_get_referral_config():
    return await rqadm.admin_get_referral_config()

@app.post("/api/admin/referral-config")
async def admin_add_referral_config(data: ReferralConfigCreate):
    return await rqadm.admin_add_referral_config(data.percent, data.is_active)

@app.patch("/api/admin/referral-config/{config_id}")
async def admin_update_referral_config(config_id: int, data: ReferralConfigUpdate):
    return await rqadm.admin_update_referral_config(config_id, data.percent, data.is_active)

@app.delete("/api/admin/referral-config/{config_id}")
async def admin_delete_referral_config(config_id: int):
    return await rqadm.admin_delete_referral_config(config_id)


# ======================
# ADMIN: ReferralEarning
# ======================
class ReferralEarningCreate(BaseModel):
    referrer_id: int
    order_id: int
    percent: int
    amount_usdt: Decimal

class ReferralEarningUpdate(BaseModel):
    referrer_id: int
    order_id: int
    percent: int
    amount_usdt: Decimal


@app.get("/api/admin/referral-earnings")
async def admin_get_referral_earnings():
    return await rqadm.admin_get_referral_earnings()

@app.post("/api/admin/referral-earnings")
async def admin_add_referral_earning(data: ReferralEarningCreate):
    return await rqadm.admin_add_referral_earning(data.dict())

@app.put("/api/admin/referral-earnings/{earning_id}")
async def admin_update_referral_earning(earning_id: int, data: ReferralEarningUpdate):
    return await rqadm.admin_update_referral_earning(earning_id, data.dict())

@app.delete("/api/admin/referral-earnings/{earning_id}")
async def admin_delete_referral_earning(earning_id: int):
    return await rqadm.admin_delete_referral_earning(earning_id)




