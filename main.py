from contextlib import asynccontextmanager
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from models import init_db, async_session, VPNKey, TypesVPN, CountriesVPN, ServersVPN, User
from sqlalchemy import select, update
import requestsfile as rq
from datetime import datetime, timedelta
from typing import List
import uuid
import os
from aiogram import Bot
from aiogram.types import LabeledPrice
from dotenv import load_dotenv
load_dotenv()

import os
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

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

class RegisterUser(BaseModel):
    tg_id: int
    userRole: str
    referrer_tg_id: int | None = None
    

@app.post("/api/register")
async def register_user(data: RegisterUser):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == data.tg_id))
        if user:
            return {"status": "exists", "idUser": user.idUser}

        referrer_id = None
        if data.referrer_tg_id:
            ref_user = await session.scalar(select(User).where(User.tg_id == data.referrer_tg_id))
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

        return {"status": "ok", "idUser": new_user.idUser, "referrer_id": referrer_id}


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


# =======================
# ADMIN MODELS
# =======================

class UserCreate(BaseModel):
    tg_id: int
    userRole: str

class UserUpdate(UserCreate): pass

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

class VPNKeyCreate(BaseModel):
    idUser: int
    idServerVPN: int
    provider: str
    provider_key_id: str
    access_data: str
    expires_at: datetime
    is_active: bool

class VPNKeyUpdate(VPNKeyCreate): pass

class VPNSubscriptionCreate(BaseModel):
    idUser: int
    vpn_key_id: int
    started_at: datetime | None = None
    expires_at: datetime
    status: str

class VPNSubscriptionUpdate(VPNSubscriptionCreate): pass

class ReferralEarningCreate(BaseModel):
    referrer_id: int
    referred_id: int
    amount: int

class ReferralEarningUpdate(ReferralEarningCreate): pass

# =======================
# USERS ADMIN
# =======================
@app.get("/api/admin/users")
async def admin_get_users():
    try:
        return await rq.admin_get_users()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/users")
async def admin_add_user(user: UserCreate):
    try:
        return await rq.admin_add_user(user.tg_id, user.userRole)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, user: UserUpdate):
    try:
        return await rq.admin_update_user(user_id, user.tg_id, user.userRole)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int):
    try:
        return await rq.admin_delete_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
    

# =======================
# VPN KEYS ADMIN
# =======================
@app.get("/api/admin/keys")
async def admin_get_keys():
    try:
        return await rq.admin_get_keys()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/keys")
async def admin_add_key(key: VPNKeyCreate):
    try:
        return await rq.admin_add_key(key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.patch("/api/admin/keys/{key_id}")
async def admin_update_key(key_id: int, key: VPNKeyUpdate):
    try:
        return await rq.admin_update_key(key_id, key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/keys/{key_id}")
async def admin_delete_key(key_id: int):
    try:
        return await rq.admin_delete_key(key_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# =======================
# VPN SUBSCRIPTIONS ADMIN
# =======================
@app.get("/api/admin/subscriptions")
async def admin_get_subscriptions():
    try:
        return await rq.admin_get_subscriptions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/subscriptions")
async def admin_add_subscription(sub: VPNSubscriptionCreate):
    try:
        return await rq.admin_add_subscription(sub)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.patch("/api/admin/subscriptions/{sub_id}")
async def admin_update_subscription(sub_id: int, sub: VPNSubscriptionUpdate):
    try:
        return await rq.admin_update_subscription(sub_id, sub)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/subscriptions/{sub_id}")
async def admin_delete_subscription(sub_id: int):
    try:
        return await rq.admin_delete_subscription(sub_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# =======================
# REFERRAL EARNINGS ADMIN
# =======================
@app.get("/api/admin/referral_earnings")
async def admin_get_referral_earnings():
    try:
        return await rq.admin_get_referral_earnings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/referral_earnings")
async def admin_add_referral_earning(e: ReferralEarningCreate):
    try:
        return await rq.admin_add_referral_earning(e)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))

@app.delete("/api/admin/referral_earnings/{earning_id}")
async def admin_delete_referral_earning(earning_id: int):
    try:
        return await rq.admin_delete_referral_earning(earning_id)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    
    
# =======================
# --- REFERRAL SYSTEM ---
# =======================
"""Возвращает количество приглашённых пользователей для данного TG ID"""
@app.get("/api/admin/referrals-count/{tg_id}")
async def get_referrals_count(tg_id: int = Path(..., description="TG ID пользователя")):
    try:
        from requestsfile import get_referrals_count
        count = await get_referrals_count(tg_id)
        return {"count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


"""Возвращает список пользователей, которых пригласил данный TG ID"""
@app.get("/api/admin/referrals/{tg_id}")
async def get_referrals(tg_id: int = Path(..., description="TG ID пользователя")):
    try:
        from requestsfile import get_referrals_list
        referrals = await get_referrals_list(tg_id)
        return referrals
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))