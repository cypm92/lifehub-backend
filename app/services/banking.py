"""Enable Banking transport, kept independent of budget assignment logic.

The transport deliberately never logs provider responses or credentials. Callers must
handle errors without returning their details to clients.
"""
from abc import ABC, abstractmethod
import json
import time
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

import jwt


class BankingProvider(ABC):
    @abstractmethod
    def request(self, method: str, path: str, *, params=None, body=None):
        """Issue a provider request and return its decoded JSON response."""


class EnableBanking(BankingProvider):
    base_url = "https://api.enablebanking.com"

    def __init__(self, app_id: str, private_key: bytes):
        self.app_id = app_id
        self.private_key = private_key

    def token(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iss": "enablebanking.com", "aud": "api.enablebanking.com",
             "iat": now, "exp": now + 3600},
            self.private_key, algorithm="RS256", headers={"kid": self.app_id},
        )

    def request(self, method: str, path: str, *, params=None, body=None):
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Authorization": "Bearer " + self.token(), "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        with urlopen(request, timeout=30) as response:
            payload = response.read()
        return json.loads(payload) if payload else {}

    def aspsps(self, country: str | None = None):
        return self.request("GET", "/aspsps", params={"country": country} if country else None)

    def authorize(self, body: dict):
        return self.request("POST", "/auth", body=body)

    def create_session(self, code: str):
        return self.request("POST", "/sessions", body={"code": code})

    def session(self, session_id: str):
        return self.request("GET", "/sessions/" + quote(session_id, safe=""))

    def revoke(self, session_id: str):
        return self.request("DELETE", "/sessions/" + quote(session_id, safe=""))

    def account_details(self, account_id: str):
        return self.request("GET", "/accounts/" + quote(account_id, safe="") + "/details")

    def balances(self, account_id: str):
        return self.request("GET", "/accounts/" + quote(account_id, safe="") + "/balances")

    def transactions(self, account_id: str, date_from: str):
        path = "/accounts/" + quote(account_id, safe="") + "/transactions"
        params = {"date_from": date_from}
        seen = set()
        while True:
            page = self.request("GET", path, params=params)
            if not isinstance(page, dict) or not isinstance(page.get("transactions"), list):
                raise ValueError("Invalid transactions page")
            yield from page["transactions"]
            key = page.get("continuation_key")
            if not key:
                return
            if not isinstance(key, str) or key in seen:
                raise ValueError("Invalid or repeated continuation key")
            seen.add(key)
            params["continuation_key"] = key
