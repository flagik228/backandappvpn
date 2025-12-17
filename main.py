from contextlib import asynccontextmanager
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models import init_db, async_session, VPNKey, TypesVPN, CountriesVPN, ServersVPN
from sqlalchemy import select, update
import requestsfile as rq
from datetime import datetime, timedelta
from typing import List
import uuid
import os
from aiogram import Bot
from aiogram.types import LabeledPrice

# --- FastAPI приложение ---
@asynccontextmanager
async def lifespan(app_: FastAPI):
    await init_db()
    print("VPN backend ready!")
    yield

app = FastAPI(title="ArtCry VPN", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(BOT_TOKEN)

async def create_stars_invoice(
    title: str,
    description: str,
    payload: str,
    amount_stars: int
) -> str:
    prices = [LabeledPrice(label=title, amount=amount_stars)]

    invoice_link = await bot.create_invoice_link(
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # Stars → пусто
        currency="XTR",
        prices=prices
    )
    return invoice_link



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
# BUY VPN
# ======================

class BuyVPN(BaseModel):
    tg_id: int
    server_id: int


@app.post("/api/vpn/stars-invoice")
async def create_invoice(data: BuyVPN):
    payload = f"buy:{data.tg_id}:{data.server_id}:{uuid.uuid4()}"

    server = await rq.get_server_by_id(data.server_id)
    if not server:
        raise HTTPException(404, "Server not found")

    invoice_url = await create_stars_invoice(
        title=f"VPN {server['nameVPN']}",
        description="VPN на 30 дней",
        payload=payload,
        amount_stars=server["price"]
    )

    return {"url": invoice_url, "payload": payload}


@app.post("/api/vpn/payment-success")
async def payment_success(payload: str):
    await rq.activate_vpn_from_payload(payload)
    return {"status": "ok"}


# ======================
# RENEW VPN
# ======================

class RenewVPN(BaseModel):
    tg_id: int
    vpn_key_id: int
    months: int


@app.post("/api/vpn/renew-invoice")
async def renew_invoice(data: RenewVPN):
    payload = f"renew:{data.tg_id}:{data.vpn_key_id}:{data.months}:{uuid.uuid4()}"
    stars = data.months * 50

    invoice_url = await create_stars_invoice(
        title="Продление VPN",
        description=f"Продление на {data.months} мес.",
        payload=payload,
        amount_stars=stars
    )

    return {"url": invoice_url, "payload": payload}


@app.post("/api/vpn/renew-success")
async def renew_success(payload: str):
    await rq.renew_vpn_from_payload(payload)
    return {"status": "ok"}


# --- MODELS REQUESTS ---
class TypeVPNCreate(BaseModel):
    nameType: str
    descriptionType: str

class TypeVPNUpdate(BaseModel):
    nameType: str
    descriptionType: str

class CountryCreate(BaseModel):
    nameCountry: str

class CountryUpdate(BaseModel):
    nameCountry: str

class ServerCreate(BaseModel):
    nameVPN: str
    price: int
    max_conn: int
    server_ip: str
    api_url: str
    api_token: str
    idTypeVPN: int
    idCountry: int
    is_active: bool

class ServerUpdate(ServerCreate):
    pass

# =======================
# --- TYPES ADMIN ---
# =======================
@app.get("/api/admin/types")
async def admin_get_types():
    try:
        return await rq.admin_get_types()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/types")
async def admin_add_type(type_data: TypeVPNCreate):
    try:
        return await rq.admin_add_type(type_data.nameType, type_data.descriptionType)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.patch("/api/admin/types/{type_id}")
async def admin_update_type(type_id: int, type_data: TypeVPNUpdate):
    try:
        return await rq.admin_update_type(type_id, type_data.nameType, type_data.descriptionType)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/types/{type_id}")
async def admin_delete_type(type_id: int):
    try:
        return await rq.admin_delete_type(type_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# =======================
# --- COUNTRIES ADMIN ---
# =======================
@app.get("/api/admin/countries")
async def admin_get_countries():
    try:
        return await rq.admin_get_countries()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/countries")
async def admin_add_country(country: CountryCreate):
    try:
        return await rq.admin_add_country(country.nameCountry)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.patch("/api/admin/countries/{country_id}")
async def admin_update_country(country_id: int, country: CountryUpdate):
    try:
        return await rq.admin_update_country(country_id, country.nameCountry)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/countries/{country_id}")
async def admin_delete_country(country_id: int):
    try:
        return await rq.admin_delete_country(country_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# =======================
# --- SERVERS ADMIN ---
# =======================
@app.get("/api/admin/servers")
async def admin_get_servers():
    try:
        return await rq.admin_get_servers()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/servers")
async def admin_add_server(server: ServerCreate):
    try:
        return await rq.admin_add_server(server)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/admin/servers/{server_id}")
async def admin_update_server(server_id: int, server: ServerUpdate):
    try:
        return await rq.admin_update_server(server_id, server)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/servers/{server_id}")
async def admin_delete_server(server_id: int):
    try:
        return await rq.admin_delete_server(server_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))





"""
# --- Модели запроса ---
class VPNInvoiceRequest(BaseModel):
    tg_id: int
    server_id: int

class VPNRenewInvoiceRequest(BaseModel):
    tg_id: int
    vpn_key_id: int
    months: int = 1
    

# --- Эндпоинты ---

# Получение списка серверов VPN
@app.get("/api/vpn/servers")
async def vpn_servers():
    return await rq.get_servers()


# После успешной оплаты Stars покупка VPN
@app.post("/api/vpn/payment-success")
async def vpn_payment_success(payload: str):
    try:
        _, user_id, server_id = payload.split("_")
        user_id, server_id = int(user_id), int(server_id)

        servers = await rq.get_servers()
        server = next((s for s in servers if s["idServerVPN"] == server_id), None)
        if not server:
            raise HTTPException(status_code=404, detail="Сервер не найден")

        result = await rq.buy_vpn(user_id, server_id, server["api_url"])
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Список VPN пользователя
@app.get("/api/vpn/my/{tg_id}")
async def vpn_my(tg_id: int):
    return await rq.get_my_vpns(tg_id)


# Генерация инвойса для покупки VPN через Stars
@app.post("/api/vpn/stars-invoice")
async def vpn_stars_invoice(request: VPNInvoiceRequest):
    user = await rq.add_user(request.tg_id, "user")
    async with async_session() as session:
        server = await session.scalar(select(ServersVPN).where(ServersVPN.idServerVPN == request.server_id))
        if not server:
            raise HTTPException(status_code=404, detail="Сервер не найден")

    payload = f"vpn30days_{user.idUser}_{server.idServerVPN}"

    return {
        "title": "VPN на 30 дней",
        "description": f"Доступ к VPN серверу {server.nameVPN} на 30 дней",
        "currency": "XTR",
        "prices": [
            { "label": "30 дней", "amount": server.price }  # цена берётся из базы, 5000 = 50 ⭐
        ],
        "payload": payload
    }

# Продление VPN через Stars - генерация инвойса
@app.post("/api/vpn/renew-invoice")
async def vpn_renew_invoice(request: VPNRenewInvoiceRequest):
    user = await rq.add_user(request.tg_id, "user")
    async with async_session() as session:
        vpn_key = await session.scalar(
            select(VPNKey).where(
                VPNKey.id == request.vpn_key_id,
                VPNKey.idUser == user.idUser,
                VPNKey.is_active == True
            )
        )
        if not vpn_key:
            raise HTTPException(status_code=404, detail="VPN ключ не найден")

    months = max(1, request.months)
    stars_amount = STARS_PRICE * months * 100  # целое число для invoice
    payload = f"renew_{user.idUser}_{vpn_key.id}_{months}"

    invoice = {
        "title": f"Продление VPN на {months} мес.",
        "description": f"Продление доступа к VPN на {months} месяц(ев)",
        "currency": CURRENCY,
        "prices": [{"label": f"{months} мес.", "amount": stars_amount}],
        "payload": payload
    }
    return invoice

# После успешной оплаты Stars продление VPN
@app.post("/api/vpn/renew-success")
async def vpn_renew_success(payload: str):
    try:
        _, user_id, vpn_key_id, months = payload.split("_")
        user_id, vpn_key_id, months = int(user_id), int(vpn_key_id), int(months)
        result = await rq.renew_vpn(user_id, vpn_key_id, months)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
"""
