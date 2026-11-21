"""Authenticated Kalshi V2 order client. Never logs credentials.

Production-only Kalshi client. NO buys are expressed as YES asks.
Orders are immediate-or-cancel limit orders, so they cannot rest indefinitely.
"""
import base64
import time
import uuid
from decimal import Decimal
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

HOSTS={"production":"https://external-api.kalshi.com"}
ROOT="/trade-api/v2"

class KalshiTrader:
    def __init__(self,key_id,private_key_pem,environment="production"):
        if environment not in HOSTS:raise ValueError("Unknown Kalshi environment")
        if not key_id or not private_key_pem:raise ValueError("Missing Kalshi credentials")
        self.environment=environment
        self.key_id=str(key_id)
        self.key=serialization.load_pem_private_key(
            str(private_key_pem).replace("\\n","\n").encode(),password=None)
        if not isinstance(self.key,(rsa.RSAPrivateKey,ed25519.Ed25519PrivateKey)):
            raise ValueError("Kalshi key must be RSA or Ed25519")

    def _headers(self,method,path):
        timestamp=str(int(time.time()*1000))
        payload=(timestamp+method.upper()+ROOT+path.split("?")[0]).encode()
        if isinstance(self.key,rsa.RSAPrivateKey):
            signature=self.key.sign(payload,padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH),hashes.SHA256())
        else:signature=self.key.sign(payload)
        return {"Content-Type":"application/json",
            "KALSHI-ACCESS-KEY":self.key_id,
            "KALSHI-ACCESS-TIMESTAMP":timestamp,
            "KALSHI-ACCESS-SIGNATURE":base64.b64encode(signature).decode()}

    def request(self,method,path,body=None):
        response=requests.request(method,HOSTS[self.environment]+ROOT+path,
            headers=self._headers(method,path),json=body,timeout=20)
        if not response.ok:
            try:
                detail=response.json()
                detail=str(detail)[:500]
            except ValueError:
                detail=response.text[:300]
            raise ValueError(f"Kalshi HTTP {response.status_code}: {detail}")
        return response.json()

    def balance(self):
        return self.request("GET","/portfolio/balance")

    def market(self,ticker):
        from urllib.parse import quote
        return self.request("GET","/markets/"+quote(ticker,safe=""))

    def balance_for_exchange(self,exchange_index):
        if exchange_index is None:
            return self.balance()
        return self.request("GET",f"/portfolio/balance?exchange_index={int(exchange_index)}")

    def buy_no_ioc(self,ticker,contracts,max_no_price_cents,client_order_id=None):
        """NO exposure = sell YES at a minimum of 1 - max NO cost."""
        if not ticker or not isinstance(ticker,str):raise ValueError("Missing ticker")
        n=int(contracts)
        if n<1 or n>100:raise ValueError("Contracts out of range")
        price=Decimal(str(max_no_price_cents))
        if price<=0 or price>=100:raise ValueError("Invalid NO limit price")
        yes_price=(Decimal("1")-price/Decimal("100"))
        if yes_price<=0 or yes_price>=1:raise ValueError("Invalid YES price")
        body={"ticker":ticker,"client_order_id":client_order_id or str(uuid.uuid4()),
              "side":"ask","count":f"{n:.2f}","price":f"{yes_price:.4f}",
              "time_in_force":"immediate_or_cancel",
              "self_trade_prevention_type":"taker_at_cross",
              "exchange_index":-1}
        return self.request("POST","/portfolio/events/orders",body)
