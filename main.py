from contextlib import asynccontextmanager
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.future import select
from pydantic import BaseModel
from decimal import Decimal
from sqlalchemy import select, update, delete
from datetime import datetime, timedelta

import requestsfile as rq
from models import init_db, async_session, User, ExchangeRate, Tariff

# ======================
# APP
# ======================

@asynccontextmanager
async def lifespan(app_: FastAPI):
    await init_db()
    print("✅ VPN backend ready!")
    yield

app = FastAPI(title="ArtCry VPN", lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    price_usdt: str
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
    pair: str
    rate: Decimal

class ExchangeRateUpdate(ExchangeRateCreate):
    pass

@app.get("/api/admin/exchange-rates")
async def admin_get_exchange_rates():
    async with async_session() as session:
        rates = await session.scalars(select(ExchangeRate))
        return [{
            "id": r.id,
            "pair": r.pair,
            "rate": str(r.rate),
            "updated_at": r.updated_at.isoformat()
        } for r in rates]

@app.post("/api/admin/exchange-rates")
async def admin_add_exchange_rate(data: ExchangeRateCreate):
    async with async_session() as session:
        rate = ExchangeRate(pair=data.pair, rate=data.rate)
        session.add(rate)
        await session.commit()
        await session.refresh(rate)
        return {
            "id": rate.id,
            "pair": rate.pair,
            "rate": str(rate.rate)
        }

@app.patch("/api/admin/exchange-rates/{rate_id}")
async def admin_update_exchange_rate(rate_id: int, data: ExchangeRateUpdate):
    async with async_session() as session:
        rate = await session.get(ExchangeRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="ExchangeRate not found")
        await session.execute(update(ExchangeRate).where(ExchangeRate.id == rate_id).values(
            pair=data.pair,
            rate=data.rate,
            updated_at=datetime.utcnow()
        ))
        await session.commit()
        return {"status": "ok"}

@app.delete("/api/admin/exchange-rates/{rate_id}")
async def admin_delete_exchange_rate(rate_id: int):
    async with async_session() as session:
        rate = await session.get(ExchangeRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="ExchangeRate not found")
        await session.delete(rate)
        await session.commit()
        return {"status": "ok"}
    
    
# ======================
# ADMIN: EXCHANGE RATE (для получения страны и типа впн в страницу с серверами)
# ======================

from fastapi import HTTPException
from decimal import Decimal
import requestsfile as rq

@app.get("/api/admin/exchange-rate/XTR_USDT")
async def get_xtr_rate():
    # Берём курс из таблицы ExchangeRate
    async with async_session() as session:
        rate = await session.scalar(
            select(rq.ExchangeRate).where(rq.ExchangeRate.pair == "XTR_USDT")
        )
        if not rate:
            raise HTTPException(status_code=404, detail="Exchange rate XTR_USDT not found")
        return {
            "pair": rate.pair,
            "rate": float(rate.rate),  # или str(rate.rate) если нужно точное Decimal
            "updated_at": rate.updated_at.isoformat()
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
    
    
# --- Оплата и продление VPN --- 
@app.post("/api/vpn/pay")
async def pay_vpn(data: OrderRequest):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            return await rq.pay_and_extend_vpn(user.idUser, data.server_id, data.tariff_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))