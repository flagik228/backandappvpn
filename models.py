from sqlalchemy import (ForeignKey, String, BigInteger, Integer,Boolean, DateTime, Numeric)
from sqlalchemy.orm import (Mapped, DeclarativeBase, mapped_column)
from sqlalchemy.ext.asyncio import (AsyncAttrs, async_sessionmaker, create_async_engine)
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Column, Integer, ForeignKey, Numeric, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from dotenv import load_dotenv
import os


load_dotenv()  # читаем .env

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_async_engine(url=DATABASE_URL, echo=True)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass

# --- USERS ---
class UserStart(Base):
    __tablename__ = "user_starts"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    referrer_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    idUser: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    tg_username: Mapped[str] = mapped_column(String(300), nullable=True)
    userRole: Mapped[str] = mapped_column(String(100), default="user")
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.idUser"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    
# --- Wallet ---
class UserWallet(Base):
    __tablename__ = "user_wallets"
    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser", ondelete="CASCADE"),unique=True)
    balance_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0.0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    transactions = relationship("WalletTransaction", back_populates="wallet", cascade="all, delete-orphan")


class WalletOperation(Base):
    __tablename__ = "wallet_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(50)) # deposit / withdrawal
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    status: Mapped[str] = mapped_column(String(50), default="pending") # pending / paid / completed / failed

    provider: Mapped[str] = mapped_column(String(50)) # stars / cryptobot / yukassa
    meta: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    payments = relationship("Payment", back_populates="wallet_operation")
    
    
class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("user_wallets.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    type: Mapped[str] = mapped_column(String(200))  # referral / deposit / withdrawal
    description: Mapped[str] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    wallet = relationship("UserWallet", back_populates="transactions")


# --- TASKS ---
class UserTask(Base):
    __tablename__ = "user_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser", ondelete="CASCADE"))
    task_key: Mapped[str] = mapped_column(String(100)) # example: welcome_bonus, first_purchase
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("idUser", "task_key", name="uq_user_task"),)


class UserReward(Base):
    __tablename__ = "user_rewards"
    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser", ondelete="CASCADE"))
    reward_type: Mapped[str] = mapped_column(String(50)) # example: vpn_days
    days: Mapped[int] = mapped_column(Integer)
    is_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_server_id: Mapped[int | None] = mapped_column(ForeignKey("servers_vpn.idServerVPN"),nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- VPN ---
class TypesVPN(Base):
    __tablename__ = "types_vpn"
    idTypeVPN: Mapped[int] = mapped_column(primary_key=True)
    nameType: Mapped[str] = mapped_column(String(200))  # outline / hiddify / vless / shadowsocks
    descriptionType: Mapped[str] = mapped_column(String(200))

    # страны
class CountriesVPN(Base):
    __tablename__ = "countries_vpn"
    idCountry: Mapped[int] = mapped_column(primary_key=True)
    nameCountry: Mapped[str] = mapped_column(String(200), nullable=False)


    # VPN SERVERS
class ServersVPN(Base):
    __tablename__ = "servers_vpn"
    idServerVPN: Mapped[int] = mapped_column(primary_key=True)
    nameVPN: Mapped[str] = mapped_column(String(200))
    price_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6))   # stars / cents
    max_conn: Mapped[int] = mapped_column(Integer)
    now_conn: Mapped[int] = mapped_column(Integer, default=0)

    server_ip: Mapped[str] = mapped_column(String(300))
    api_url: Mapped[str] = mapped_column(String(300))
    api_token: Mapped[str] = mapped_column(String(300))
    xui_username: Mapped[str] = mapped_column(String(300))
    xui_password: Mapped[str] = mapped_column(String(300))
    inbound_port: Mapped[int] = mapped_column(Integer)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    idTypeVPN: Mapped[int] = mapped_column(ForeignKey("types_vpn.idTypeVPN"))
    idCountry: Mapped[int] = mapped_column(ForeignKey("countries_vpn.idCountry"))
    
    tariffs = relationship("Tariff", back_populates="server", cascade="all, delete-orphan")
    
    
    # тарифы
class Tariff(Base):
    __tablename__ = "tariffs"
    idTarif: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers_vpn.idServerVPN", ondelete="CASCADE"))
    days: Mapped[int] = mapped_column(Integer)  # 1, 7, 14, 30
    price_tarif: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    server = relationship("ServersVPN", back_populates="tariffs")
    
    # курсы
class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    pair: Mapped[str] = mapped_column(String(100), unique=True)  # "XTR_USDT", "RUB_USDT"
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))   # 0.01301886
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    
    # заказы
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser"))
    server_id: Mapped[int] = mapped_column(ForeignKey("servers_vpn.idServerVPN"))
    idTarif: Mapped[int] = mapped_column(ForeignKey("tariffs.idTarif"))
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_subscriptions.id"),nullable=True)
    purpose_order: Mapped[str] = mapped_column(String(100)) # "buy", "extension"
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(100))  # XTR / USDT
    provider: Mapped[str] = mapped_column(String(50), default="unknown")  # stars / cryptobot / yookassa / balance
    payment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending / paid / failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


    # оплата заказа
class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=True)
    wallet_operation_id: Mapped[int] = mapped_column(ForeignKey("wallet_operations.id", ondelete="CASCADE"), nullable=True)
    provider: Mapped[str] = mapped_column(String(200))  # stars / cryptobot
    provider_payment_id: Mapped[str] = mapped_column(String(200))   # ID платежа у платёжного провайдера.
    status: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    wallet_operation = relationship("WalletOperation", back_populates="payments")


class VPNSubscription(Base):
    __tablename__ = "vpn_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser"))
    idServerVPN: Mapped[int] = mapped_column(ForeignKey("servers_vpn.idServerVPN"))

    provider: Mapped[str] = mapped_column(String(100))
    provider_client_email: Mapped[str] = mapped_column(String(200), index=True)
    provider_client_uuid: Mapped[str] = mapped_column(String(200))
    access_data: Mapped[str] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="active")  # active / expired


# --- REFERALS ---
class ReferralConfig(Base):
    __tablename__ = "referral_config"
    id: Mapped[int] = mapped_column(primary_key=True)
    percent: Mapped[int] = mapped_column(Integer)  # 5 = 5%
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # реферальные начисления
class ReferralEarning(Base):
    __tablename__ = "referral_earnings"
    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.idUser"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    percent: Mapped[int] = mapped_column(Integer)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)