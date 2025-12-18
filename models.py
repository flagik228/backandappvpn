from sqlalchemy import (ForeignKey, String, BigInteger, Integer,Boolean, DateTime, Numeric)
from sqlalchemy.orm import (Mapped, DeclarativeBase, mapped_column)
from sqlalchemy.ext.asyncio import (AsyncAttrs, async_sessionmaker, create_async_engine)
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Column, Integer, ForeignKey, Numeric, Boolean
from sqlalchemy.orm import relationship


engine = create_async_engine(url='sqlite+aiosqlite:///db.sqlite3', echo = True)

async_session = async_sessionmaker(bind=engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass


    # юзеры
class User(Base):
    __tablename__ = "users"
    idUser: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    userRole: Mapped[str] = mapped_column(String(100), default="user")
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.idUser"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    
    # внутрений баланс пользователя
class UserWallet(Base):
    __tablename__ = "user_wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser", ondelete="CASCADE"),unique=True)
    balance_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0.0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    
    # история транзакций баланса
class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("user_wallets.id", ondelete="CASCADE"))

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    type: Mapped[str] = mapped_column(String(100))  # referral / deposit / withdrawal

    description: Mapped[str] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # категории VPN
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
    
    # курс stars
class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    pair: Mapped[str] = mapped_column(String(50), unique=True)  # "XTR_USDT"
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))   # 0.01301886
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    
    # заказы
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser"))
    server_id: Mapped[int] = mapped_column(ForeignKey("servers_vpn.idServerVPN"))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(10))  # XTR / USDT
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending / paid / failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


    # оплата заказа
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))

    provider: Mapped[str] = mapped_column(String(100))  # stars / cryptobot

    provider_payment_id: Mapped[str] = mapped_column(String(200))   # ID платежа у платёжного провайдера.
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


    # VPN KEYS
class VPNKey(Base):
    __tablename__ = "vpn_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser"))
    idServerVPN: Mapped[int] = mapped_column(ForeignKey("servers_vpn.idServerVPN"))

    provider: Mapped[str] = mapped_column(String(200))
    provider_key_id: Mapped[str] = mapped_column(String(200))
    access_data: Mapped[str] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


    # VPN SUBSCRIPTIONS
class VPNSubscription(Base):
    __tablename__ = "vpn_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    idUser: Mapped[int] = mapped_column(ForeignKey("users.idUser"))
    vpn_key_id: Mapped[int] = mapped_column(ForeignKey("vpn_keys.id"))

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="active")


    # конфиг для рефералки
class ReferralConfig(Base):
    __tablename__ = "referral_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    percent: Mapped[int] = mapped_column(Integer)  # 5 = 5%
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # реферальные начисления
class ReferralEarning(Base):
    __tablename__ = "referral_earnings"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.idUser"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    percent: Mapped[int] = mapped_column(Integer)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)