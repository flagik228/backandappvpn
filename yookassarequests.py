# yookassarequests.py
from yookassa import Configuration, Payment
from decimal import Decimal
import uuid
import os


Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")

async def create_yookassa_payment(order_id: int,amount_rub: Decimal,description: str):
    payment = Payment.create({
        "amount": {"value": str(amount_rub.quantize(Decimal("0.01"))),"currency": "RUB"},
        "confirmation": {"type": "redirect","return_url": os.getenv("YOOKASSA_RETURN_URL")},
        "capture": True,"description": description,
        "metadata": {"order_id": str(order_id)}
    }, uuid.uuid4())

    return payment.id, payment.confirmation.confirmation_url
