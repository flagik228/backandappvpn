from aiocryptopay import AioCryptoPay, Networks
import os

token = os.getenv("CRYPTOPAY_TOKEN")
crypto = AioCryptoPay(token, network=Networks.TEST_NET)