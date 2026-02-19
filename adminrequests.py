from sqlalchemy import select, update, delete
from models import (async_session, User, UserWallet, WalletTransaction, VPNSubscription, TypesVPN,
    CountriesVPN, ServersVPN, Tariff, ExchangeRate, Order, Payment, ReferralConfig, ReferralEarning,
    PromoCode, PromoCodeUsage, BundlePlan, BundleServer, BundleTariff, WalletOperation,
    UserFreeDaysBalance, UserCheckin, UserTask, UserReward, UserRewardOp)
from typing import List
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import func
from urllib.parse import quote

from xui_api import XUIApi
import tasksrequests as taskrq

# --- ADMIN ------------------------------------------------------------

# =======================
# --- ADMIN: USERS ---
# =======================
async def admin_get_users():
    async with async_session() as session:
        users = await session.scalars(select(User))
        return [{
            "idUser": u.idUser,
            "tg_id": u.tg_id,
            "tg_username": u.tg_username,
            "userRole": u.userRole,
            "referrer_id": u.referrer_id,
            "created_at": u.created_at.isoformat()
        } for u in users]
        
async def admin_add_user(tg_id: int,tg_username: str | None,userRole: str,referrer_id: int | None):
    async with async_session() as session:
        user = User(
            tg_id=tg_id,
            tg_username=tg_username,
            userRole=userRole,
            referrer_id=referrer_id
        )
        session.add(user)
        await session.flush()

        session.add(UserWallet(idUser=user.idUser))
        await session.commit()
        return {"idUser": user.idUser}

async def admin_update_user(user_id: int, data: dict):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        for field in ["tg_id", "tg_username", "userRole", "referrer_id"]:
            if field in data:
                setattr(user, field, data[field])

        await session.commit()
        return {"status": "ok"}

async def admin_delete_user(user_id: int):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        await session.delete(user)
        await session.commit()
        return {"status": "ok"}


def _iso(value: datetime | None):
    return value.isoformat() if value else None


def _history_ts(dt: datetime | None) -> float:
    if not dt:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _order_title(purpose: str):
    mapping = {
        "buy": "Покупка подписки",
        "bundle_buy": "Покупка bundle-плана",
        "extension": "Продление подписки",
        "bundle_extension": "Продление bundle-подписки"
    }
    return mapping.get(purpose, f"Заказ: {purpose}")


def _wallet_tx_title(tx_type: str):
    mapping = {
        "referral": "Реферальное начисление",
        "promo": "Начисление по промокоду",
        "deposit": "Пополнение баланса",
        "withdrawal": "Списание с баланса",
    }
    return mapping.get(tx_type, f"Операция кошелька: {tx_type}")


def _reward_op_title(source: str):
    mapping = {
        "task": "Награда за выполнение задания",
        "referral_signup": "Награда за реферала",
        "checkin": "Ежедневный check-in",
        "checkin_exchange": "Обмен check-in на FREE дни",
        "activate": "Активация FREE дней",
        "legacy_rewards": "Импорт/начисление legacy-наград",
        "promo": "Начисление FREE дней по промокоду",
    }
    return mapping.get(source, f"Операция FREE дней: {source}")


async def admin_get_user_details(user_id: int, history_limit: int = 200):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        wallet = await session.scalar(select(UserWallet).where(UserWallet.idUser == user_id))
        free_days = await session.scalar(select(UserFreeDaysBalance).where(UserFreeDaysBalance.idUser == user_id))
        checkin = await session.scalar(select(UserCheckin).where(UserCheckin.idUser == user_id))

        completed_tasks = (await session.scalars(
            select(UserTask).where(UserTask.idUser == user_id)
        )).all()
        completed_task_map = {t.task_key: t.completed_at for t in completed_tasks}

        tasks_payload = []
        for task in taskrq.TASKS:
            completed_at = completed_task_map.get(task["key"])
            tasks_payload.append({
                "key": task["key"],
                "title": task["title"],
                "reward_days": task["reward_days"],
                "completed": task["key"] in completed_task_map,
                "completed_at": _iso(completed_at)
            })

        orders = (await session.scalars(
            select(Order)
            .where(Order.idUser == user_id)
            .order_by(Order.created_at.desc())
            .limit(history_limit)
        )).all()

        wallet_ops = (await session.scalars(
            select(WalletOperation)
            .where(WalletOperation.idUser == user_id)
            .order_by(WalletOperation.created_at.desc())
            .limit(history_limit)
        )).all()

        wallet_txs = []
        if wallet:
            wallet_txs = (await session.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == wallet.id)
                .order_by(WalletTransaction.created_at.desc())
                .limit(history_limit)
            )).all()

        reward_ops = (await session.scalars(
            select(UserRewardOp)
            .where(UserRewardOp.idUser == user_id)
            .order_by(UserRewardOp.created_at.desc())
            .limit(history_limit)
        )).all()

        rewards = (await session.scalars(
            select(UserReward)
            .where(UserReward.idUser == user_id)
            .order_by(UserReward.created_at.desc())
            .limit(history_limit)
        )).all()

        promo_rows = (await session.execute(
            select(PromoCodeUsage, PromoCode)
            .join(PromoCode, PromoCode.id == PromoCodeUsage.promo_code_id)
            .where(PromoCodeUsage.idUser == user_id)
            .order_by(PromoCodeUsage.created_at.desc())
            .limit(history_limit)
        )).all()

        task_by_key = {task["key"]: task for task in taskrq.TASKS}
        history_items = []

        for order in orders:
            history_items.append((_history_ts(order.created_at), {
                "id": order.id,
                "type": "order",
                "title": _order_title(order.purpose_order),
                "description": f"Провайдер: {order.provider or 'unknown'}",
                "status": order.status,
                "amount_usdt": str(order.amount),
                "days_delta": None,
                "source": "orders",
                "meta": None,
                "created_at": _iso(order.created_at)
            }))

        for op in wallet_ops:
            history_items.append((_history_ts(op.created_at), {
                "id": op.id,
                "type": "wallet_operation",
                "title": _wallet_tx_title(op.type),
                "description": op.meta,
                "status": op.status,
                "amount_usdt": str(op.amount_usdt),
                "days_delta": None,
                "source": "wallet_operations",
                "meta": op.meta,
                "created_at": _iso(op.created_at)
            }))

        for tx in wallet_txs:
            history_items.append((_history_ts(tx.created_at), {
                "id": tx.id,
                "type": "wallet_transaction",
                "title": _wallet_tx_title(tx.type),
                "description": tx.description,
                "status": "completed",
                "amount_usdt": str(tx.amount),
                "days_delta": None,
                "source": "wallet_transactions",
                "meta": tx.description,
                "created_at": _iso(tx.created_at)
            }))

        for row in completed_tasks:
            task_info = task_by_key.get(row.task_key, {})
            history_items.append((_history_ts(row.completed_at), {
                "id": row.id,
                "type": "task_completed",
                "title": f"Задание выполнено: {task_info.get('title', row.task_key)}",
                "description": f"Ключ задания: {row.task_key}",
                "status": "completed",
                "amount_usdt": None,
                "days_delta": task_info.get("reward_days"),
                "source": "user_tasks",
                "meta": row.task_key,
                "created_at": _iso(row.completed_at)
            }))

        for rop in reward_ops:
            history_items.append((_history_ts(rop.created_at), {
                "id": rop.id,
                "type": "reward_operation",
                "title": _reward_op_title(rop.source),
                "description": rop.meta,
                "status": "completed",
                "amount_usdt": None,
                "days_delta": rop.days_delta,
                "source": "user_reward_ops",
                "meta": rop.meta,
                "created_at": _iso(rop.created_at)
            }))

        for reward in rewards:
            if not reward.is_activated or not reward.activated_at:
                continue
            history_items.append((_history_ts(reward.activated_at), {
                "id": reward.id,
                "type": "reward_activation",
                "title": "Активация награды FREE дней",
                "description": f"{reward.days} дн. на сервер #{reward.activated_server_id}",
                "status": "completed",
                "amount_usdt": None,
                "days_delta": reward.days,
                "source": "user_rewards",
                "meta": None,
                "created_at": _iso(reward.activated_at)
            }))

        for usage, promo in promo_rows:
            history_items.append((_history_ts(usage.created_at), {
                "id": usage.id,
                "type": "promo_activation",
                "title": "Активация промокода",
                "description": f"{promo.code} ({promo.reward_name})",
                "status": "completed",
                "amount_usdt": str(promo.reward_value) if promo.reward_type == "balance" else None,
                "days_delta": int(promo.reward_value) if promo.reward_type == "free_days" else None,
                "source": "promo_code_usages",
                "meta": promo.code,
                "created_at": _iso(usage.created_at)
            }))

        history_items.sort(key=lambda item: item[0], reverse=True)
        history_payload = [item for _, item in history_items[:history_limit]]

        return {
            "user": {
                "idUser": user.idUser,
                "tg_id": user.tg_id,
                "tg_username": user.tg_username,
                "userRole": user.userRole,
                "referrer_id": user.referrer_id,
                "created_at": _iso(user.created_at),
            },
            "wallet": {
                "balance_usdt": str(wallet.balance_usdt) if wallet else "0",
                "updated_at": _iso(wallet.updated_at) if wallet else None,
            },
            "free_days": {
                "balance_days": free_days.balance_days if free_days else 0,
                "updated_at": _iso(free_days.updated_at) if free_days else None,
            },
            "checkin": {
                "checkin_count": checkin.checkin_count if checkin else 0,
                "last_checkin_at": _iso(checkin.last_checkin_at) if checkin else None,
            },
            "tasks": tasks_payload,
            "reward_operations": [{
                "id": rop.id,
                "source": rop.source,
                "days_delta": rop.days_delta,
                "meta": rop.meta,
                "created_at": _iso(rop.created_at)
            } for rop in reward_ops],
            "reward_activations": [{
                "id": reward.id,
                "days": reward.days,
                "activated_server_id": reward.activated_server_id,
                "activated_at": _iso(reward.activated_at),
                "created_at": _iso(reward.created_at),
                "is_activated": reward.is_activated
            } for reward in rewards],
            "history": history_payload
        }
        
        
# =======================
# --- ADMIN: UserWallet ---
# =======================
async def admin_get_wallets():
    async with async_session() as session:
        wallets = (await session.scalars(select(UserWallet))).all()
        return [{
            "id": w.id,
            "idUser": w.idUser,
            "balance_usdt": str(w.balance_usdt),
            "updated_at": w.updated_at.isoformat()
        } for w in wallets]

async def admin_add_wallet(data: dict):
    async with async_session() as session:
        wallet = UserWallet(**data)
        session.add(wallet)
        await session.commit()
        await session.refresh(wallet)
        return {"id": wallet.id}

async def admin_update_wallet(wallet_id: int, data: dict):
    async with async_session() as session:
        wallet = await session.get(UserWallet, wallet_id)
        if not wallet:
            raise ValueError("Wallet not found")

        for k, v in data.items():
            setattr(wallet, k, v)

        wallet.updated_at = datetime.utcnow()
        await session.commit()
        return {"status": "ok"}

async def admin_delete_wallet(wallet_id: int):
    async with async_session() as session:
        wallet = await session.get(UserWallet, wallet_id)
        if not wallet:
            raise ValueError("Wallet not found")

        await session.delete(wallet)
        await session.commit()
        return {"status": "ok"}
    
    

# =======================
# --- ADMIN: WalletTransaction ---
# =======================
async def admin_get_wallet_transactions():
    async with async_session() as session:
        txs = (await session.scalars(select(WalletTransaction))).all()
        return [{
            "id": t.id,
            "wallet_id": t.wallet_id,
            "amount": str(t.amount),
            "type": t.type,
            "description": t.description,
            "created_at": t.created_at.isoformat()
        } for t in txs]

async def admin_add_wallet_transaction(data: dict):
    async with async_session() as session:
        tx = WalletTransaction(**data)
        session.add(tx)
        await session.commit()
        await session.refresh(tx)
        return {"id": tx.id}

async def admin_update_wallet_transaction(tx_id: int, data: dict):
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx:
            raise ValueError("Transaction not found")

        for k, v in data.items():
            setattr(tx, k, v)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_wallet_transaction(tx_id: int):
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx:
            raise ValueError("Transaction not found")

        await session.delete(tx)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: TYPES VPN (CRUD)
# =========================================================
async def admin_get_types():
    async with async_session() as session:
        types = await session.scalars(select(TypesVPN))
        return [{
            "idTypeVPN": t.idTypeVPN,
            "nameType": t.nameType,
            "descriptionType": t.descriptionType
        } for t in types]

async def admin_add_type(nameType: str, descriptionType: str):
    if not nameType or not descriptionType:
        raise ValueError("nameType и descriptionType обязательны")

    async with async_session() as session:
        t = TypesVPN(nameType=nameType, descriptionType=descriptionType)
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return {
            "idTypeVPN": t.idTypeVPN,
            "nameType": t.nameType,
            "descriptionType": t.descriptionType
        }

async def admin_update_type(type_id: int, nameType: str, descriptionType: str):
    async with async_session() as session:
        t = await session.get(TypesVPN, type_id)
        if not t:
            raise ValueError("TypeVPN не найден")

        await session.execute(update(TypesVPN).where(TypesVPN.idTypeVPN == type_id)
            .values(
                nameType=nameType,
                descriptionType=descriptionType
            )
        )
        await session.commit()
        return {"status": "ok"}

async def admin_delete_type(type_id: int):
    async with async_session() as session:
        t = await session.get(TypesVPN, type_id)
        if not t:
            raise ValueError("TypeVPN не найден")

        await session.delete(t)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: COUNTRIES VPN (CRUD)
# =========================================================
async def admin_get_countries():
    async with async_session() as session:
        countries = await session.scalars(select(CountriesVPN))
        return [{
            "idCountry": c.idCountry,
            "nameCountry": c.nameCountry
        } for c in countries]

async def admin_add_country(nameCountry: str):
    if not nameCountry:
        raise ValueError("nameCountry обязателен")

    async with async_session() as session:
        c = CountriesVPN(nameCountry=nameCountry)
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {
            "idCountry": c.idCountry,
            "nameCountry": c.nameCountry
        }

async def admin_update_country(country_id: int, nameCountry: str):
    async with async_session() as session:
        c = await session.get(CountriesVPN, country_id)
        if not c:
            raise ValueError("CountryVPN не найден")

        await session.execute(update(CountriesVPN).where(CountriesVPN.idCountry == country_id)
            .values(nameCountry=nameCountry)
        )
        await session.commit()
        return {"status": "ok"}

async def admin_delete_country(country_id: int):
    async with async_session() as session:
        c = await session.get(CountriesVPN, country_id)
        if not c:
            raise ValueError("CountryVPN не найден")

        await session.delete(c)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: SERVERS VPN
# =========================================================
async def admin_get_servers():
    async with async_session() as session:
        servers = await session.scalars(select(ServersVPN))
        result = []
        for s in servers:
            result.append({
                "idServerVPN": s.idServerVPN,
                "nameVPN": s.nameVPN,
                "price_usdt": str(s.price_usdt),
                "max_conn": s.max_conn,
                "now_conn": s.now_conn,
                "server_ip": s.server_ip,
                "api_url": s.api_url,
                "xui_username": s.xui_username,
                "xui_password": s.xui_password,
                "inbound_port": s.inbound_port,
                "subscription_port": s.subscription_port,
                "is_active": s.is_active,
                "idTypeVPN": s.idTypeVPN,
                "idCountry": s.idCountry
            })
        return result

async def admin_add_server(data):
    async with async_session() as session:
        server = ServersVPN(
            nameVPN=data.nameVPN,
            price_usdt=data.price_usdt,
            max_conn=data.max_conn,
            now_conn=0,  # при создании новый сервер всегда 0
            server_ip=data.server_ip,
            api_url=data.api_url,
            xui_username=data.xui_username,
            xui_password=data.xui_password,
            inbound_port=data.inbound_port,
            subscription_port=data.subscription_port,
            is_active=data.is_active,
            idTypeVPN=data.idTypeVPN,
            idCountry=data.idCountry
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        return {
            "idServerVPN": server.idServerVPN,
            "nameVPN": server.nameVPN
        }

async def admin_update_server(server_id: int, data):
    async with async_session() as session:
        server = await session.get(ServersVPN, server_id)
        if not server:
            raise ValueError("ServerVPN не найден")

        await session.execute(
            update(ServersVPN)
            .where(ServersVPN.idServerVPN == server_id)
            .values(
                nameVPN=data["nameVPN"],
                price_usdt=data["price_usdt"],
                max_conn=data["max_conn"],
                server_ip=data["server_ip"],
                api_url=data["api_url"],
                xui_username=data["xui_username"],
                xui_password=data["xui_password"],
                inbound_port=data["inbound_port"],
                subscription_port=data["subscription_port"],
                is_active=data["is_active"],
                idTypeVPN=data["idTypeVPN"],
                idCountry=data["idCountry"]
            )
        )
        await session.commit()
        return {"status": "ok"}

async def admin_delete_server(server_id: int):
    async with async_session() as session:
        server = await session.get(ServersVPN, server_id)
        if not server:
            raise ValueError("ServerVPN не найден")

        await session.delete(server)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: Tariff
# =========================================================
async def admin_get_tariffs(server_id: int):
    async with async_session() as session:
        tariffs = await session.scalars(select(Tariff).where(Tariff.server_id == server_id))
        return [{
            "idTarif": t.idTarif,
            "server_id": t.server_id,
            "days": t.days,
            "price_tarif": str(t.price_tarif),
            "is_active": t.is_active
        } for t in tariffs]

async def admin_add_tariff(server_id: int, days: int, price_tarif: Decimal, is_active: bool):
    async with async_session() as session:
        t = Tariff(
            server_id=server_id,
            days=days,
            price_tarif=price_tarif,
            is_active=is_active
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return {"idTarif": t.idTarif}

async def admin_update_tariff(tariff_id: int, days: int, price_tarif: Decimal, is_active: bool):
    async with async_session() as session:
        t = await session.get(Tariff, tariff_id)
        if not t:
            raise ValueError("Tariff not found")

        t.days = days
        t.price_tarif = price_tarif
        t.is_active = is_active
        await session.commit()
        return {"status": "ok"}

async def admin_delete_tariff(tariff_id: int):
    async with async_session() as session:
        t = await session.get(Tariff, tariff_id)
        if not t:
            raise ValueError("Tariff not found")

        await session.delete(t)
        await session.commit()
        return {"status": "ok"}


# =========================================================
# --- ADMIN: Bundle Tariffs
# =========================================================
async def admin_get_bundle_tariffs(bundle_plan_id: int):
    async with async_session() as session:
        tariffs = await session.scalars(
            select(BundleTariff).where(BundleTariff.bundle_plan_id == bundle_plan_id)
        )
        return [{
            "id": t.id,
            "bundle_plan_id": t.bundle_plan_id,
            "days": t.days,
            "price_usdt": str(t.price_usdt),
            "is_active": t.is_active
        } for t in tariffs]


async def admin_add_bundle_tariff(data: dict):
    async with async_session() as session:
        t = BundleTariff(
            bundle_plan_id=data["bundle_plan_id"],
            days=data["days"],
            price_usdt=data["price_usdt"],
            is_active=data.get("is_active", True)
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return {"id": t.id}


async def admin_update_bundle_tariff(tariff_id: int, data: dict):
    async with async_session() as session:
        t = await session.get(BundleTariff, tariff_id)
        if not t:
            raise ValueError("BundleTariff not found")

        for field in ["days", "price_usdt", "is_active"]:
            if field in data:
                setattr(t, field, data[field])

        await session.commit()
        return {"status": "ok"}


async def admin_delete_bundle_tariff(tariff_id: int):
    async with async_session() as session:
        t = await session.get(BundleTariff, tariff_id)
        if not t:
            raise ValueError("BundleTariff not found")
        await session.delete(t)
        await session.commit()
        return {"status": "ok"}


# =========================================================
# --- ADMIN: Bundle Plans
# =========================================================
async def admin_get_bundle_plans():
    async with async_session() as session:
        plans = (await session.scalars(select(BundlePlan))).all()
        result = []
        for p in plans:
            servers = (await session.scalars(
                select(BundleServer.server_id).where(BundleServer.bundle_plan_id == p.id)
            )).all()
            result.append({
                "id": p.id,
                "name": p.name,
                "price_usdt": str(p.price_usdt),
                "is_active": p.is_active,
                "server_ids": servers
            })
        return result


async def admin_add_bundle_plan(data: dict):
    async with async_session() as session:
        plan = BundlePlan(
            name=data["name"],
            price_usdt=data["price_usdt"],
            is_active=data.get("is_active", True)
        )
        session.add(plan)
        await session.flush()

        for server_id in data.get("server_ids", []):
            session.add(BundleServer(bundle_plan_id=plan.id, server_id=server_id))

        await session.commit()
        await session.refresh(plan)
        return {"id": plan.id}


async def admin_update_bundle_plan(plan_id: int, data: dict):
    async with async_session() as session:
        plan = await session.get(BundlePlan, plan_id)
        if not plan:
            raise ValueError("BundlePlan not found")

        for field in ["name", "price_usdt", "is_active"]:
            if field in data:
                setattr(plan, field, data[field])

        if "server_ids" in data:
            await session.execute(
                delete(BundleServer).where(BundleServer.bundle_plan_id == plan_id)
            )
            for server_id in data["server_ids"]:
                session.add(BundleServer(bundle_plan_id=plan_id, server_id=server_id))

        await session.commit()
        return {"status": "ok"}


async def admin_delete_bundle_plan(plan_id: int):
    async with async_session() as session:
        plan = await session.get(BundlePlan, plan_id)
        if not plan:
            raise ValueError("BundlePlan not found")
        await session.delete(plan)
        await session.commit()
        return {"status": "ok"}
    


# =========================================================
# --- ADMIN: EXCHANGE RATES (CRUD)
# =========================================================
async def admin_get_exchange_rate(pair: str):
    async with async_session() as session:
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == pair))
        if not rate:
            return None

        return {
            "pair": rate.pair,
            "rate": str(rate.rate),
            "updated_at": rate.updated_at.isoformat()
        }

async def admin_set_exchange_rate(pair: str, rate_value: Decimal):
    async with async_session() as session:
        rate = await session.scalar(select(ExchangeRate).where(ExchangeRate.pair == pair))

        if rate:
            rate.rate = rate_value
            rate.updated_at = datetime.utcnow()
        else:
            rate = ExchangeRate(pair=pair,rate=rate_value)
            session.add(rate)

        await session.commit()

        return {
            "pair": rate.pair,
            "rate": str(rate.rate),
            "updated_at": rate.updated_at.isoformat()
        }
    

    
# =========================================================
# --- ADMIN: Order
# =========================================================
ALLOWED_PURPOSES = {"buy", "extension"}

async def admin_get_orders():
    async with async_session() as session:
        orders = (await session.scalars(select(Order))).all()
        return [{
            "id": o.id,
            "idUser": o.idUser,
            "server_id": o.server_id,
            "idTarif": o.idTarif,
            "purpose_order": o.purpose_order,
            "amount": o.amount,
            "currency": o.currency,
            "status": o.status,
            "created_at": o.created_at.isoformat()
        } for o in orders]

async def admin_add_order(data):
    if data.get("purpose_order") not in ALLOWED_PURPOSES:
        raise ValueError("Invalid purpose_order")
    
    async with async_session() as session:
        order = Order(**data)
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return {"id": order.id}

async def admin_update_order(order_id: int, data: dict):
    if "purpose_order" in data:
        if data["purpose_order"] not in ALLOWED_PURPOSES:
            raise ValueError("Invalid purpose_order")
        
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            raise ValueError("Order not found")

        for key, value in data.items():
            setattr(order, key, value)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_order(order_id: int):
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            raise ValueError("Order not found")

        await session.delete(order)
        await session.commit()
        return {"status": "ok"}

async def admin_get_all_tariffs():
    async with async_session() as session:
        tariffs = (await session.scalars(select(Tariff))).all()
        return [
            {
                "idTarif": t.idTarif,
                "server_id": t.server_id,
                "days": t.days,
                "price_tarif": str(t.price_tarif),
                "is_active": t.is_active
            }
            for t in tariffs
        ]



# =========================================================
# --- ADMIN: Payment
# =========================================================
async def admin_get_payments():
    async with async_session() as session:
        payments = (await session.scalars(select(Payment))).all()
        return [{
            "id": p.id,
            "order_id": p.order_id,
            "provider": p.provider,
            "provider_payment_id": p.provider_payment_id,
            "status": p.status,
            "created_at": p.created_at.isoformat()
        } for p in payments]

async def admin_add_payment(data: dict):
    async with async_session() as session:
        payment = Payment(**data)
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return {"id": payment.id}

async def admin_update_payment(payment_id: int, data: dict):
    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            raise ValueError("Payment not found")

        for key, value in data.items():
            setattr(payment, key, value)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_payment(payment_id: int):
    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            raise ValueError("Payment not found")

        await session.delete(payment)
        await session.commit()
        return {"status": "ok"}


# =========================================================
# --- ADMIN: VPNSubscription
# =========================================================
async def admin_get_vpn_subscriptions():
    async with async_session() as session:
        subs = (await session.scalars(select(VPNSubscription))).all()

        return [{
            "id": s.id,
            "idUser": s.idUser,
            "idServerVPN": s.idServerVPN,

            "provider": s.provider,
            "provider_client_email": s.provider_client_email,
            "provider_client_uuid": s.provider_client_uuid,
            "subscription_id": s.subscription_id,
            "subscription_url": s.subscription_url,

            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),

            "is_active": s.is_active,
            "status": s.status,
        } for s in subs]


async def admin_add_vpn_subscription(data: dict):
    async with async_session() as session:
        sub = VPNSubscription(
            idUser=data["idUser"],
            idServerVPN=data["idServerVPN"],
            provider=data["provider"],
            provider_client_email=data["provider_client_email"],
            provider_client_uuid=data["provider_client_uuid"],
            subscription_id=data.get("subscription_id"),
            subscription_url=data.get("subscription_url"),
            expires_at=data["expires_at"],
            is_active=data.get("is_active", True),
            status=data.get("status", "active"),
            created_at=datetime.utcnow()
        )

        session.add(sub)
        await session.commit()
        await session.refresh(sub)

        return {"id": sub.id}


async def admin_update_vpn_subscription(sub_id: int, data: dict):
    async with async_session() as session:
        sub = await session.get(VPNSubscription, sub_id)
        if not sub:
            raise ValueError("Subscription not found")

        # обновляем ТОЛЬКО разрешённые поля
        allowed_fields = {
            "expires_at",
            "is_active",
            "status",
            "provider_client_email",
            "provider_client_uuid",
            "subscription_id",
            "subscription_url",
        }

        for key, value in data.items():
            if key in allowed_fields and value is not None:
                setattr(sub, key, value)

        await session.commit()
        return {"status": "ok"}


async def admin_delete_vpn_subscription(sub_id: int):
    async with async_session() as session:
        sub = await session.get(VPNSubscription, sub_id)
        if not sub:
            raise ValueError("Subscription not found")

        await session.delete(sub)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: ReferralConfig
# =========================================================
async def admin_get_referral_config():
    async with async_session() as session:
        rows = await session.scalars(select(ReferralConfig))
        return [{
            "id": c.id,
            "percent": c.percent,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat()
        } for c in rows]

async def admin_add_referral_config(percent: int, is_active: bool):
    async with async_session() as session:

        if is_active:
            await session.execute(
                update(ReferralConfig).values(is_active=False)
            )

        c = ReferralConfig(percent=percent, is_active=is_active)
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {"id": c.id}

async def admin_update_referral_config(config_id: int, percent: int, is_active: bool):
    async with async_session() as session:
        c = await session.get(ReferralConfig, config_id)
        if not c:
            raise ValueError("ReferralConfig not found")

        if is_active:
            await session.execute(
                update(ReferralConfig)
                .where(ReferralConfig.id != config_id)
                .values(is_active=False)
            )

        c.percent = percent
        c.is_active = is_active
        await session.commit()
        return {"status": "ok"}

async def admin_delete_referral_config(config_id: int):
    async with async_session() as session:
        c = await session.get(ReferralConfig, config_id)
        if not c:
            raise ValueError("Config not found")

        await session.delete(c)
        await session.commit()
        return {"status": "ok"}



# =========================================================
# --- ADMIN: ReferralEarning
# =========================================================
async def admin_get_referral_earnings():
    async with async_session() as session:
        earnings = (await session.scalars(select(ReferralEarning))).all()
        return [{
            "id": e.id,
            "referrer_id": e.referrer_id,
            "order_id": e.order_id,
            "percent": e.percent,
            "amount_usdt": str(e.amount_usdt),
            "created_at": e.created_at.isoformat()
        } for e in earnings]

async def admin_add_referral_earning(data: dict):
    async with async_session() as session:
        e = ReferralEarning(**data)
        session.add(e)
        await session.commit()
        await session.refresh(e)
        return {"id": e.id}

async def admin_update_referral_earning(earning_id: int, data: dict):
    async with async_session() as session:
        e = await session.get(ReferralEarning, earning_id)
        if not e:
            raise ValueError("ReferralEarning not found")

        for k, v in data.items():
            setattr(e, k, v)

        await session.commit()
        return {"status": "ok"}

async def admin_delete_referral_earning(earning_id: int):
    async with async_session() as session:
        e = await session.get(ReferralEarning, earning_id)
        if not e:
            raise ValueError("ReferralEarning not found")

        await session.delete(e)
        await session.commit()
        return {"status": "ok"}


# =======================
# --- ADMIN: PROMO CODES ---
# =======================
def normalize_promo_code(code: str) -> str:
    return code.strip().upper()


async def admin_get_promo_codes():
    async with async_session() as session:
        promos = await session.scalars(select(PromoCode))
        return [{
            "id": p.id,
            "code": p.code,
            "reward_type": p.reward_type,
            "reward_value": str(p.reward_value),
            "reward_name": p.reward_name,
            "max_uses": p.max_uses,
            "used_count": p.used_count,
            "is_active": p.is_active,
            "created_at": p.created_at.isoformat()
        } for p in promos]


async def admin_add_promo_code(code: str, reward_type: str, reward_value: Decimal, reward_name: str, max_uses: int | None, is_active: bool):
    if not code:
        raise ValueError("code is required")
    if reward_type not in ("balance", "free_days"):
        raise ValueError("reward_type must be balance or free_days")

    async with async_session() as session:
        promo = PromoCode(
            code=code,
            code_normalized=normalize_promo_code(code),
            reward_type=reward_type,
            reward_value=reward_value,
            reward_name=reward_name,
            max_uses=max_uses,
            is_active=is_active
        )
        session.add(promo)
        await session.commit()
        await session.refresh(promo)
        return {"id": promo.id}


async def admin_update_promo_code(promo_id: int, data: dict):
    async with async_session() as session:
        promo = await session.get(PromoCode, promo_id)
        if not promo:
            raise ValueError("Promo code not found")

        if "code" in data and data["code"] is not None:
            promo.code = data["code"]
            promo.code_normalized = normalize_promo_code(data["code"])

        for field in ["reward_type", "reward_value", "reward_name", "max_uses", "is_active"]:
            if field in data:
                setattr(promo, field, data[field])

        await session.commit()
        return {"status": "ok"}


async def admin_delete_promo_code(promo_id: int):
    async with async_session() as session:
        promo = await session.get(PromoCode, promo_id)
        if not promo:
            raise ValueError("Promo code not found")

        await session.delete(promo)
        await session.commit()
        return {"status": "ok"}