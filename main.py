from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal


from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, update, delete

from aiogram import Bot, Dispatcher, F
from aiogram.types import Update, PreCheckoutQuery, Message, LabeledPrice
from aiogram.methods import CreateInvoiceLink

import requestsfile as rq
import adminrequests as rqadm
from requestsfile import create_order, pay_and_extend_vpn, create_vpn_xui
from models import init_db, async_session, User, ExchangeRate, Tariff, ServersVPN, Order, VPNKey, UserWallet, Payment

BOT_TOKEN = "8423828272:AAHGuxxQEvTELPukIXl2eNL3p25fI9GGx0U"
WEBHOOK_PATH = "/webhook"

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ======================
# APP
# ======================
@asynccontextmanager
async def lifespan(app_: FastAPI):
    await init_db()
    print("✅ VPN backend ready!")
    yield

app = FastAPI(title="ArtCry VPN", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],)


# ======================
# TELEGRAM WEBHOOK
# ======================
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}



# ======================
# REGISTER
# ======================

class RegisterUser(BaseModel):
    tg_id: int
    referrer_tg_id: int | None = None


@app.post("/api/register")
async def register_user(data: RegisterUser):
    async with async_session() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == data.tg_id)
        )
        if user:
            return {
                "status": "exists",
                "idUser": user.idUser,
                "referrer_id": user.referrer_id
            }

        referrer_id = None
        if data.referrer_tg_id and data.referrer_tg_id != data.tg_id:
            ref_user = await session.scalar(
                select(User).where(User.tg_id == data.referrer_tg_id)
            )
            if ref_user:
                referrer_id = ref_user.idUser

        user = User(
            tg_id=data.tg_id,
            userRole="user",
            referrer_id=referrer_id
        )
        session.add(user)
        await session.flush()

        wallet = UserWallet(idUser=user.idUser, balance_usdt=Decimal("0.0"))
        session.add(wallet)

        await session.commit()
        await session.refresh(user)

        return {
            "status": "ok",
            "idUser": user.idUser,
            "referrer_id": referrer_id
        }



# ======================
# TELEGRAM HANDLERS
# ======================
@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload

    try:
        prefix, order_id_str = payload.split(":")
        order_id = int(order_id_str)
    except:
        return

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "pending":
            return

        order.status = "paid"

        # Payment
        payment = Payment(
            order_id=order.id,
            provider="telegram_stars",
            provider_payment_id=message.successful_payment.telegram_payment_charge_id,
            status="paid"
        )
        session.add(payment)
        await session.flush()

        order.status = "processing"

        tariff = await session.get(Tariff, order.idTarif)
        if not tariff:
            order.status = "failed"
            await session.commit()
            await message.answer("❌ Тариф не найден")
            return

        try:
            vpn_data = await create_vpn_xui(
                order.idUser,
                order.server_id,
                tariff.days
            )
        except Exception as e:
            order.status = "failed"
            await session.commit()
            await message.answer(f"❌ Ошибка VPN: {e}")
            return

        order.status = "completed"
        await session.commit()

        server = await session.get(ServersVPN, order.server_id)
        await message.answer(
            f"✅ <b>VPN активирован!</b>\n"
            f"Сервер: {server.nameVPN}\n"
            f"Действует до: {vpn_data['expires_at_human']}\n\n"
            f"<b>Ваш ключ:</b>\n"
            f"<code>{vpn_data['access_data']}</code>",
            parse_mode="HTML"
        )
        """
        await message.answer(
            f"✅ VPN активирован!\n"
            f"Сервер: {server.nameVPN}\n"
            f"Действует до: {vpn_data['expires_at_human']}\n"
            f"Ваш ключ:\n{vpn_data['access_data']}"
        )
        """
        
# API
# ======================

class CreateInvoiceRequest(BaseModel):
    tg_id: int
    tariff_id: int

@app.post("/api/vpn/create_invoice")
async def create_invoice(data: CreateInvoiceRequest):
    async with async_session() as session:
        # 1) Проверка пользователя
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # 2) Тариф
        tariff = await session.scalar(select(Tariff).where(Tariff.idTarif == data.tariff_id))
        if not tariff or not tariff.is_active:
            raise HTTPException(status_code=404, detail="Tariff not found")

        # 3) Сервер
        server = await session.scalar(select(ServersVPN).where(ServersVPN.idServerVPN == tariff.server_id))
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        # 4) Курс XTR/USDT
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        if not rate:
            raise HTTPException(status_code=400, detail="Exchange rate not set")

        # 5) Расчёт Stars
        price_usdt = Decimal(tariff.price_tarif)
        rate_usdt = Decimal(rate.rate)
        stars_price = int(price_usdt / rate_usdt)
        if stars_price < 1:
            stars_price = 1

        # 6) Создаём Order и добавляем в сессию
        order = Order(
            idUser=user.idUser,
            server_id=server.idServerVPN,
            idTarif=tariff.idTarif,  # ← добавляем тариф
            amount=stars_price,
            currency="XTR",
            status="pending"
        )
        session.add(order)
        await session.flush()  # присвоит order.idOrder
        await session.commit() # сохранение окончательно
        
        # 7) Возврат данных для фронта (Telegram Mini App)
        
        invoice_link = await bot(
            CreateInvoiceLink(
                title=f"VPN {tariff.days} дней",
                description=server.nameVPN,
                payload=f"vpn:{order.id}",
                currency="XTR",
                prices=[LabeledPrice(label=f"{tariff.days} дней VPN", amount=stars_price)]
            )
        )
        return {"invoice_link": invoice_link} 



# -------- ПРОДЛЕНИЕ

class RenewInvoiceRequest(BaseModel):
    tg_id: int
    vpn_key_id: int
    tariff_id: int

@app.post("/api/vpn/renew-invoice")
async def renew_invoice(data: RenewInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(404, "User not found")

        vpn_key = await session.get(VPNKey, data.vpn_key_id)
        if not vpn_key:
            raise HTTPException(404, "VPN key not found")

        tariff = await session.get(Tariff, data.tariff_id)
        if not tariff or not tariff.is_active:
            raise HTTPException(404, "Tariff not found")

        server = await session.get(ServersVPN, vpn_key.idServerVPN)

        rate = await session.scalar(
            select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT")
        )
        if not rate:
            raise HTTPException(500, "Exchange rate not set")

        stars_price = int(Decimal(tariff.price_tarif) / rate.rate)
        if stars_price < 1:
            stars_price = 1

        order = Order(
            idUser=user.idUser,
            server_id=server.idServerVPN,
            idTarif=tariff.idTarif,
            amount=stars_price,
            currency="XTR",
            status="pending",
            type="renew"   # 🔥 ВАЖНО
        )

        session.add(order)
        await session.flush()
        await session.commit()

        invoice_link = await bot(
            CreateInvoiceLink(
                title=f"Продление VPN {tariff.days} дней",
                description=server.nameVPN,
                payload=f"renew:{order.id}",
                currency="XTR",
                prices=[
                    LabeledPrice(
                        label=f"{tariff.days} дней VPN",
                        amount=stars_price
                    )
                ]
            )
        )

        return {"invoice_link": invoice_link}
        
        
# подтверждение оплаты продления
@app.post("/api/vpn/renew-success")
async def renew_success(payload: str):
    prefix, order_id = payload.split(":")
    order_id = int(order_id)

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "pending":
            raise HTTPException(400, "Invalid order")

        order.status = "paid"

        payment = Payment(
            order_id=order.id,
            provider="telegram_stars",
            provider_payment_id="renew",
            status="paid"
        )
        session.add(payment)
        await session.flush()

        order.status = "processing"

        result = await pay_and_extend_vpn(
            order.idUser,
            order.server_id,
            order.idTarif
        )

        order.status = "completed"
        await session.commit()

        return {"status": "ok", "vpn": result}



# --- Создание заказа Stars --- 
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

        # Конвертация USDT -> Stars (берём из ExchangeRate)
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        if not rate:
            raise HTTPException(status_code=500, detail="Exchange rate not found")

        amount_stars = int(tariff.price_tarif / rate.rate)

        return await rq.create_order(user.idUser, data.server_id, data.tariff_id, Decimal(amount_stars), currency="XTR")
    


# ======================
# PUBLIC
# ======================

@app.get("/api/vpn/servers")
async def get_servers():
    return await rq.get_servers()

@app.get("/api/vpn/my/{tg_id}")
async def my_vpns(tg_id: int):
    return await rq.get_my_vpns(tg_id)

# получение тарифов сервера
@app.get("/api/vpn/tariffs/{server_id}")
async def get_tariffs(server_id: int):
    try:
        return await rq.get_server_tariffs(server_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

# REFERRALS
# ======================
@app.get("/api/admin/referrals-count/{tg_id}")
async def get_referrals_count(
    tg_id: int = Path(..., description="TG ID пользователя")
):
    count = await rq.get_referrals_count(tg_id)
    return {"count": count}


@app.get("/api/admin/referrals/{tg_id}")
async def get_referrals(
    tg_id: int = Path(..., description="TG ID пользователя")
):
    return await rq.get_referrals_list(tg_id)








# =========================================================================================================================================
# ADMIN MODELS
# ======================

# ======================
# ADMIN: USERS
# ======================
class AdminUserUpdate(BaseModel):
    userRole: str

class AdminUserCreate(BaseModel):
    tg_id: int
    userRole: str = "user"
    referrer_id: int | None = None

class AdminUserUpdate(BaseModel):
    userRole: str


@app.get("/api/admin/users")
async def admin_get_users():
    return await rqadm.admin_get_users()

@app.post("/api/admin/users")
async def admin_add_user(data: AdminUserCreate):
    return await rqadm.admin_add_user(tg_id=data.tg_id, userRole=data.userRole, referrer_id=data.referrer_id)

@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, data: AdminUserUpdate):
    return await rqadm.admin_update_user(user_id, data.userRole)

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
class ExchangeRateCreate(BaseModel):
    rate: Decimal
    rate_to_usdt: Decimal
    
@app.get("/api/admin/exchange-rates")
async def admin_get_exchange_rates():
    return await rqadm.admin_get_exchange_rates()

@app.post("/api/admin/exchange-rates")
async def admin_add_exchange_rate(data: ExchangeRateCreate):
    return await rqadm.admin_add_exchange_rate(data.currency,data.rate_to_usdt)

@app.patch("/api/admin/exchange-rates/{rate_id}")
async def admin_update_exchange_rate(rate_id: int, data: ExchangeRateCreate):
    return await rqadm.admin_update_exchange_rate(rate_id, data.rate_to_usdt)

@app.delete("/api/admin/exchange-rates/{rate_id}")
async def admin_delete_exchange_rate(rate_id: int):
    return await rqadm.admin_delete_exchange_rate(rate_id)



# ======================
# ADMIN: Order
# ======================
class OrderCreate(BaseModel):
    idUser: int
    server_id: int
    idTarif: int
    amount: int
    currency: str
    status: str = "pending"

class OrderUpdate(BaseModel):
    idUser: int
    server_id: int
    idTarif: int
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
# ADMIN: VPN KEYS
# ======================
class VPNKeyCreate(BaseModel):
    idUser: int
    idServerVPN: int
    provider: str
    provider_key_id: str
    access_data: str
    expires_at: datetime
    is_active: bool = True

class VPNKeyUpdate(BaseModel):
    idUser: int
    idServerVPN: int
    provider: str
    provider_key_id: str
    access_data: str
    expires_at: datetime
    is_active: bool


@app.get("/api/admin/vpn-keys")
async def admin_get_vpn_keys():
    return await rqadm.admin_get_vpn_keys()

@app.post("/api/admin/vpn-keys")
async def admin_add_vpn_key(data: VPNKeyCreate):
    return await rqadm.admin_add_vpn_key(data.dict())

@app.put("/api/admin/vpn-keys/{key_id}")
async def admin_update_vpn_key(key_id: int, data: VPNKeyUpdate):
    return await rqadm.admin_update_vpn_key(key_id, data.dict())

@app.delete("/api/admin/vpn-keys/{key_id}")
async def admin_delete_vpn_key(key_id: int):
    return await rqadm.admin_delete_vpn_key(key_id)


# ======================
# ADMIN: VPN SUBSCRIPTIONS
# ======================
class VPNSubscriptionCreate(BaseModel):
    idUser: int
    vpn_key_id: int
    started_at: datetime
    expires_at: datetime
    status: str

class VPNSubscriptionUpdate(BaseModel):
    idUser: int
    vpn_key_id: int
    started_at: datetime
    expires_at: datetime
    status: str


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




