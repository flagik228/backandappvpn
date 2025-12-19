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
from models import init_db, async_session, User, ExchangeRate, Tariff, ServersVPN, Order, VPNKey, UserWallet


# ======================
# CONFIG
# ======================

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================
# TELEGRAM WEBHOOK
# ======================

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

# ======================
# TELEGRAM HANDLERS
# ======================

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload  # vpn:<order_id>
    order_id = int(payload.split(":")[1])

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "pending":
            return

        user = await session.get(User, order.idUser)
        server = await session.get(ServersVPN, order.server_id)
        tariff = await session.get(Tariff, order.tariff_id)

        now = datetime.utcnow()

        vpn_key = await session.scalar(
            select(VPNKey).where(
                VPNKey.idUser == user.idUser,
                VPNKey.idServerVPN == server.idServerVPN
            )
        )

        if vpn_key:
            if vpn_key.expires_at and vpn_key.expires_at > now:
                vpn_key.expires_at += timedelta(days=tariff.days)
            else:
                vpn_key.expires_at = now + timedelta(days=tariff.days)
            vpn_key.is_active = True
        else:
            vpn_key = VPNKey(
                idUser=user.idUser,
                idServerVPN=server.idServerVPN,
                provider="local",
                access_data="generated_access_data",
                created_at=now,
                expires_at=now + timedelta(days=tariff.days),
                is_active=True
            )
            session.add(vpn_key)

        order.status = "completed"
        await session.commit()

        await message.answer(
            f"✅ VPN активирован\n"
            f"Сервер: {server.nameVPN}\n"
            f"Действует до: {vpn_key.expires_at.strftime('%d.%m.%Y')}"
        )

# ======================
# API
# ======================

class CreateInvoiceRequest(BaseModel):
    tg_id: int
    tariff_id: int

@app.post("/api/vpn/create_invoice")
async def create_invoice(data: CreateInvoiceRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(404, "User not found")

        tariff = await session.get(Tariff, data.tariff_id)
        if not tariff or not tariff.is_active:
            raise HTTPException(404, "Tariff not found")

        server = await session.get(ServersVPN, tariff.server_id)
        rate = await session.scalar(
            select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT")
        )
        if not rate:
            raise HTTPException(400, "Exchange rate not set")

        stars = int(Decimal(tariff.price_tarif) / Decimal(rate.rate))
        stars = max(stars, 1)

        order = Order(
            idUser=user.idUser,
            server_id=server.idServerVPN,
            tariff_id=tariff.idTarif,
            amount=stars,
            currency="XTR",
            status="pending"
        )
        session.add(order)
        await session.flush()

        invoice_link = await bot(
            CreateInvoiceLink(
                title=f"VPN {tariff.days} дней",
                description=server.nameVPN,
                payload=f"vpn:{order.idOrder}",
                currency="XTR",
                prices=[LabeledPrice(label="VPN", amount=stars)]
            )
        )

        await session.commit()

        return {"invoice_link": invoice_link}





# ======================
# PUBLIC
# ======================

@app.get("/api/vpn/servers")
async def get_servers():
    return await rq.get_servers()


@app.get("/api/vpn/my/{tg_id}")
async def my_vpns(tg_id: int):
    return await rq.get_my_vpns(tg_id)

# ======================
# REGISTER
# ======================

class RegisterUser(BaseModel):
    tg_id: int
    userRole: str
    referrer_tg_id: int | None = None


@app.post("/api/register")
async def register_user(data: RegisterUser):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id)
        )
        if user:
            return {
                "status": "exists",
                "idUser": user.idUser,
                "referrer_id": user.referrer_id
            }

        referrer_id = None
        if data.referrer_tg_id and data.referrer_tg_id != data.tg_id:
            ref_user = await session.scalar(select(User).where(User.tg_id == data.referrer_tg_id)
            )
            if ref_user:
                referrer_id = ref_user.idUser

        new_user = User(
            tg_id=data.tg_id,
            userRole=data.userRole,
            referrer_id=referrer_id
        )
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)

        return {
            "status": "ok",
            "idUser": new_user.idUser,
            "referrer_id": referrer_id
        }

# ======================
# ADMIN MODELS
# ======================

class TypeVPNCreate(BaseModel):
    nameType: str
    descriptionType: str


class CountryCreate(BaseModel):
    nameCountry: str


class ServerCreate(BaseModel):
    nameVPN: str
    price_usdt: Decimal
    max_conn: int
    server_ip: str
    api_url: str
    api_token: str
    idTypeVPN: int
    idCountry: int
    is_active: bool


class ServerUpdate(ServerCreate):
    pass

# ======================
# ADMIN: TYPES
# ======================

@app.get("/api/admin/types")
async def admin_get_types():
    return await rq.admin_get_types()


@app.post("/api/admin/types")
async def admin_add_type(data: TypeVPNCreate):
    return await rq.admin_add_type(
        data.nameType,
        data.descriptionType
    )


@app.patch("/api/admin/types/{type_id}")
async def admin_update_type(type_id: int, data: TypeVPNCreate):
    return await rq.admin_update_type(
        type_id,
        data.nameType,
        data.descriptionType
    )


@app.delete("/api/admin/types/{type_id}")
async def admin_delete_type(type_id: int):
    return await rq.admin_delete_type(type_id)

# ======================
# ADMIN: COUNTRIES
# ======================

@app.get("/api/admin/countries")
async def admin_get_countries():
    return await rq.admin_get_countries()


@app.post("/api/admin/countries")
async def admin_add_country(data: CountryCreate):
    return await rq.admin_add_country(data.nameCountry)


@app.patch("/api/admin/countries/{country_id}")
async def admin_update_country(country_id: int, data: CountryCreate):
    return await rq.admin_update_country(
        country_id,
        data.nameCountry
    )


@app.delete("/api/admin/countries/{country_id}")
async def admin_delete_country(country_id: int):
    return await rq.admin_delete_country(country_id)

# ======================
# ADMIN: SERVERS
# ======================

@app.get("/api/admin/servers")
async def admin_get_servers():
    return await rq.admin_get_servers()

@app.post("/api/admin/servers")
async def admin_add_server(server: ServerCreate):
    return await rq.admin_add_server(server)

@app.patch("/api/admin/servers/{server_id}")
async def admin_update_server(server_id: int, server: ServerUpdate):
    return await rq.admin_update_server(server_id, server)

@app.delete("/api/admin/servers/{server_id}")
async def admin_delete_server(server_id: int):
    return await rq.admin_delete_server(server_id)


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
    return await rq.get_server_tariffs(server_id)

@app.post("/api/admin/tariffs")
async def admin_add_tariff(data: TariffCreate):
    async with async_session() as session:
        tariff = Tariff(
            server_id=data.server_id,
            days=data.days,
            price_tarif=data.price_tarif,
            is_active=data.is_active
        )
        session.add(tariff)
        await session.commit()
        await session.refresh(tariff)
        return {
            "idTarif": tariff.idTarif,
            "server_id": tariff.server_id,
            "days": tariff.days,
            "price_tarif": str(tariff.price_tarif),
            "is_active": tariff.is_active
        }

@app.patch("/api/admin/tariffs/{tariff_id}")
async def admin_update_tariff(tariff_id: int, data: TariffUpdate):
    async with async_session() as session:
        tariff = await session.get(Tariff, tariff_id)
        if not tariff:
            raise HTTPException(status_code=404, detail="Tariff not found")
        await session.execute(update(Tariff).where(Tariff.idTarif == tariff_id).values(
            server_id=data.server_id,
            days=data.days,
            price_tarif=data.price_tarif,
            is_active=data.is_active
        ))
        await session.commit()
        return {"status": "ok"}

@app.delete("/api/admin/tariffs/{tariff_id}")
async def admin_delete_tariff(tariff_id: int):
    async with async_session() as session:
        tariff = await session.get(Tariff, tariff_id)
        if not tariff:
            raise HTTPException(status_code=404, detail="Tariff not found")
        await session.delete(tariff)
        await session.commit()
        return {"status": "ok"}
    
# ======================
# ADMIN: ExchangeRate
# ======================
class ExchangeRateCreate(BaseModel):
    rate: Decimal
    
@app.get("/api/admin/exchange-rate/{pair}")
async def get_exchange_rate(pair: str):
    async with async_session() as session:
        rate = await session.scalar(
            select(ExchangeRate).where(ExchangeRate.pair == pair)
        )
        if not rate:
            return None

        return {
            "pair": rate.pair,
            "rate": float(rate.rate),
            "updated_at": rate.updated_at.isoformat()
        }


@app.patch("/api/admin/exchange-rate/{pair}")
async def set_exchange_rate(pair: str, data: ExchangeRateCreate):
    async with async_session() as session:
        rate = await session.scalar(
            select(ExchangeRate).where(ExchangeRate.pair == pair)
        )

        if rate is None:
            rate = ExchangeRate(
                pair=pair,
                rate=data.rate
            )
            session.add(rate)
        else:
            rate.rate = data.rate
            rate.updated_at = datetime.utcnow()

        await session.commit()

        return {
            "pair": pair,
            "rate": float(rate.rate)
        }



# ======================
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


# --- ПОЛУЧЕНИЕ ТАРИФОВ СЕРВЕРА --- 
@app.get("/api/vpn/tariffs/{server_id}")
async def get_tariffs(server_id: int):
    try:
        return await rq.get_server_tariffs(server_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    
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
    
    
    
class VPNPayRequest(BaseModel):
    tg_id: int
    tariff_id: int
    
"""
@app.post("/api/vpn/create_invoice")
async def create_invoice(data: VPNPayRequest):
    async with async_session() as session:
        # 1) Проверка пользователя
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(404, "User not found")

        # 2) Тариф
        tariff = await session.scalar(select(Tariff).where(Tariff.idTarif == data.tariff_id))
        if not tariff or not tariff.is_active:
            raise HTTPException(404, "Tariff not found")

        # 3) Сервер
        server = await session.scalar(select(ServersVPN).where(ServersVPN.idServerVPN == tariff.server_id))
        if not server:
            raise HTTPException(404, "Server not found")

        # 4) Курс
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == "XTR_USDT"))
        if not rate:
            raise HTTPException(400, "Exchange rate not set")

        # 5) Расчёт Stars
        price_usdt = Decimal(tariff.price_tarif)
        rate_usdt = Decimal(rate.rate)
        stars_price = int(price_usdt / rate_usdt)
        if stars_price < 1:
            stars_price = 1

        # 6) Создаём Order
        order = Order(
            idUser=user.idUser,
            server_id=server.idServerVPN,
            amount=stars_price,
            currency="XTR",
            status="pending"
        )
        session.add(order)
        await session.flush()
        await session.commit()

        # 7) Возврат данных для фронта
        return {
            "order_id": order.idOrder,
            "title": f"VPN {tariff.days} дней",
            "description": f"{server.nameVPN}",
            "payload": f"vpn:{order.idOrder}",
            "currency": "XTR",
            "prices": [{"label": f"{tariff.days} дней VPN", "amount": stars_price}]
        }
"""