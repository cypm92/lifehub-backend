import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import banking_models as bm, models, security
from app.config import settings
from app.database import Base, get_db
from app.routers import banking
from app.services.banking import EnableBanking
from app.services.banking_sync import transaction_key


class FakeProvider:
    def __init__(self):
        self.payload = None
        self.revoked = []
        self.exchanges = 0
        self.pages = {}
        self.requests = []

    def aspsps(self):
        return {"aspsps": [{"name": "Revolut", "country": "LT"},
                            {"name": "BBVA", "country": "ES"},
                            {"name": "Banco Sabadell", "country": "ES"},
                            {"name": "Another bank", "country": "ES"}]}

    def authorize(self, payload):
        self.payload = payload
        return {"url": "https://auth.enablebanking.com/test"}

    def create_session(self, code):
        self.exchanges += 1
        return {"session_id": "sensitive-session", "access": {"valid_until": "2099-01-01T00:00:00Z"},
                "aspsp": self.payload["aspsp"], "accounts": [{"uid": "account-1"}]}

    def session(self, session_id):
        return {"status": "AUTHORIZED", "access": {"valid_until": "2099-01-01T00:00:00Z"},
                "accounts": ["account-1", "account-2"],
                "accounts_data": [{"uid": "account-1", "name": "Primera"}, {"uid": "account-2", "name": "Segunda"}]}

    def account_details(self, uid):
        return {"uid": uid, "currency": "EUR"}

    def balances(self, uid):
        return {"balances": [{"balance_amount": {"amount": "42", "currency": "EUR"}}]}

    def request(self, method, path, *, params=None, body=None):
        self.requests.append((method, path, dict(params or {})))
        return self.pages.get((path.split("/")[2], (params or {}).get("continuation_key")), {"transactions": []})

    def transactions(self, uid, date_from):
        return EnableBanking.transactions(self, uid, date_from)

    def revoke(self, session_id):
        self.revoked.append(session_id)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.db.add_all([models.User(id=1, name="One", email="one@example.com", hashed_password="x"),
                         models.User(id=2, name="Two", email="two@example.com", hashed_password="x")])
        self.db.commit()
        self.user = self.db.get(models.User, 1)
        self.provider = FakeProvider()
        self.cipher = Fernet(Fernet.generate_key())
        self.app = FastAPI()
        self.app.include_router(banking.router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[security.get_current_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.patch_config = patch.object(banking, "configuration", return_value=(self.provider, self.cipher))
        self.patch_config.start()
        self.patch_redirect = patch.object(settings, "enable_banking_redirect_url", "https://example.com/banks/callback")
        self.patch_redirect.start()

    def tearDown(self):
        self.patch_redirect.stop()
        self.patch_config.stop()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def start(self):
        response = self.client.post("/banks/auth", json={"name": "Revolut", "country": "LT"})
        self.assertEqual(response.status_code, 200, response.text)
        return self.provider.payload["state"]

    def test_filter_and_callback_security(self):
        banks = self.client.get("/banks/aspsps").json()["banks"]
        self.assertEqual(len(banks), 3)
        self.assertIn({"name": "Revolut", "country": "LT"}, banks)
        state = self.start()
        self.user = self.db.get(models.User, 2)
        self.assertEqual(self.client.post("/banks/callback", json={"code": "code", "state": state}).status_code, 400)
        self.assertEqual(self.provider.exchanges, 0)
        self.user = self.db.get(models.User, 1)
        url = "https://example.com/banks/callback?" + urlencode({"code": "code", "state": state})
        response = self.client.post("/banks/callback", json={"return_url": url})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("sensitive-session", response.text)
        connection = self.db.query(bm.BankConnection).one()
        self.assertNotIn("sensitive-session", connection.session_encrypted)
        self.assertEqual(self.cipher.decrypt(connection.session_encrypted.encode()), b"sensitive-session")
        self.assertEqual(self.db.query(bm.BankAccount).count(), 1)
        self.assertEqual(self.client.post("/banks/callback", json={"code": "code", "state": state}).status_code, 400)
        self.assertEqual(self.provider.exchanges, 1)
        self.user = self.db.get(models.User, 2)
        self.assertEqual(self.client.get("/banks/connections").json(), [])
        self.assertEqual(self.client.delete(f"/banks/connections/{connection.id}").status_code, 404)
        self.user = self.db.get(models.User, 1)
        listing = self.client.get("/banks/connections")
        self.assertNotIn("sensitive-session", listing.text)
        self.assertEqual(listing.json()[0]["status"], "active")
        with patch.object(self.provider, "session", side_effect=OSError()):
            self.assertEqual(self.client.get("/banks/connections").json()[0]["status"], "error")
        self.assertEqual(self.client.get("/banks/connections").json()[0]["status"], "active")
        self.assertEqual(self.client.delete(f"/banks/connections/{connection.id}").status_code, 200)
        self.assertEqual(self.provider.revoked, ["sensitive-session"])

    def test_state_and_secrets_never_exposed(self):
        state = self.start()
        invalid = self.client.post("/banks/callback", json={"code": "x", "state": "incorrect"})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.provider.exchanges, 0)
        duplicate = self.client.post("/banks/callback", json={"return_url": "https://example.com/banks/callback?code=x&state=" + state + "&state=other"})
        self.assertEqual(duplicate.status_code, 400)
        response = self.client.post("/banks/callback", json={"code": "x", "state": state})
        self.assertEqual(response.status_code, 200)
        connection = self.db.query(bm.BankConnection).one()
        public = [response.text, self.client.get("/banks/status").text, self.client.get("/banks/connections").text,
                  self.client.get(f"/banks/connections/{connection.id}/accounts").text,
                  self.client.get("/banks/coverage").text,
                  self.client.post(f"/banks/connections/{connection.id}/sync").text]
        for body in public:
            self.assertNotIn("sensitive-session", body)
            self.assertNotIn(connection.session_encrypted, body)
            self.assertNotIn("session_id", body)
            self.assertNotIn("session_encrypted", body)
        self.assertEqual(self.client.post("/banks/callback", json={"code": "x", "state": state}).status_code, 400)
        self.assertEqual(self.provider.exchanges, 1)

    def test_expired_state_and_url_validation(self):
        state = self.start()
        auth = self.db.query(bm.BankAuthorization).one()
        auth.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()
        self.assertEqual(self.client.post("/banks/callback", json={"code": "x", "state": state}).status_code, 400)
        self.assertEqual(self.client.post("/banks/callback", json={"return_url": "https://evil.example/banks/callback?code=x&state=" + state}).status_code, 400)
        self.assertEqual(self.provider.exchanges, 0)

    def test_sync_idempotence_coverage_and_isolation(self):
        state = self.start()
        connection_id = self.client.post("/banks/callback", json={"code": "x", "state": state}).json()["id"]
        first = {"transaction_id": "id-1", "credit_debit_indicator": "DBIT", "status": "BOOK",
                 "booking_date": "2026-01-01", "value_date": "2026-01-02", "merchant_category_code": "5511",
                 "remittance_information": ["Café"], "transaction_amount": {"amount": "3", "currency": "EUR"},
                 "creditor": {"name": "Local"}, "creditor_account": {"iban": "ES123"}}
        second = {"entry_reference": "entry-2", "status": "PDNG", "remittance_information": ["Otro"],
                  "transaction_amount": {"amount": "5", "currency": "EUR"}, "transaction_date": "2026-01-03"}
        fallback = {"transaction_amount": {"amount": "10", "currency": "EUR"}, "booking_date": "2026-01-04",
                    "remittance_information": ["Sin ID"], "credit_debit_indicator": "CRDT", "debtor": {"name": "Pago"}}
        self.provider.pages = {
            ("account-1", None): {"transactions": [first, second], "continuation_key": "page-2"},
            ("account-1", "page-2"): {"transactions": [fallback, second]},
            ("account-2", None): {"transactions": [first]},
        }
        url = f"/banks/connections/{connection_id}/sync"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["created"], 4)
        self.assertEqual(response.json()["accounts"], 2)
        self.assertEqual(response.json()["date_from"], (datetime.now().date() - timedelta(days=90)).isoformat())
        self.assertEqual(self.provider.requests[1][2]["continuation_key"], "page-2")
        self.assertEqual(self.db.query(bm.BankTransaction).count(), 4)
        self.assertEqual(self.client.post(url).json()["created"], 0)
        self.assertEqual(self.db.query(bm.BankTransaction).count(), 4)
        updated = {**first, "note": "Actualizado"}
        self.provider.pages[("account-1", None)] = {"transactions": [updated, second], "continuation_key": "page-2"}
        self.assertEqual(self.client.post(url).json()["updated"], 1)
        first_account = self.db.query(bm.BankAccount).filter_by(provider_uid="account-1").one()
        updated_tx = self.db.query(bm.BankTransaction).filter_by(account_id=first_account.id, dedupe_key=transaction_key("account-1", first)).one()
        self.assertEqual(updated_tx.raw_data["note"], "Actualizado")
        stored = self.db.query(bm.BankTransaction).filter_by(dedupe_key=transaction_key("account-1", fallback)).one()
        self.assertEqual(stored.raw_data, fallback)
        accounts = self.client.get(f"/banks/connections/{connection_id}/accounts").json()
        self.assertEqual(len(accounts), 2)
        self.assertEqual(accounts[0]["balances"]["balances"][0]["balance_amount"]["amount"], "42")
        result = self.client.get("/banks/coverage").json()["banks"][0]
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["fields"]["counterparty"]["present"], 3)
        self.assertEqual(result["fields"]["counterparty_iban"]["present"], 2)
        self.assertEqual(result["fields"]["booking_date"]["present"], 3)
        self.assertEqual(result["statuses"], {"BOOK": 2, "PDNG": 1})
        self.assertEqual(result["accounts"][0]["total"], 3)
        self.assertEqual(self.client.get(f"/banks/accounts/{accounts[0]['id']}/transactions").json()["total"], 3)
        self.user = self.db.get(models.User, 2)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(self.client.get("/banks/coverage").json()["banks"], [])
        self.assertEqual(self.client.get(f"/banks/accounts/{accounts[0]['id']}/transactions").status_code, 404)

    def test_sync_rolls_back_partial_provider_failure(self):
        state = self.start()
        connection_id = self.client.post("/banks/callback", json={"code": "x", "state": state}).json()["id"]
        self.provider.pages = {("account-1", None): {"transactions": [{"transaction_id": "one"}]}}
        original = self.provider.balances
        def balances(uid):
            if uid == "account-2":
                raise OSError("do not expose this error")
            return original(uid)
        with patch.object(self.provider, "balances", side_effect=balances):
            result = self.client.post(f"/banks/connections/{connection_id}/sync")
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("do not expose", result.text)
        self.assertEqual(self.db.query(bm.BankTransaction).count(), 0)
        self.assertEqual(self.db.query(bm.BankAccount).count(), 1)
        self.assertIsNone(self.db.query(bm.BankAccount).one().balances)
        self.assertEqual(self.db.get(bm.BankConnection, connection_id).status, "error")

    def test_not_configured_and_renew(self):
        with patch.object(banking, "configuration", return_value=None):
            self.assertEqual(self.client.get("/banks/status").json(), {"configured": False})
            self.assertEqual(self.client.get("/banks/aspsps").status_code, 503)
        state = self.start()
        connection = self.client.post("/banks/callback", json={"code": "x", "state": state}).json()
        response = self.client.post(f"/banks/connections/{connection['id']}/renew")
        self.assertEqual(response.status_code, 200)
        new_state = self.provider.payload["state"]
        self.assertEqual(self.client.post("/banks/callback", json={"code": "new", "state": new_state}).status_code, 200)
        self.assertEqual(self.provider.revoked, ["sensitive-session"])
        self.assertEqual(self.db.get(bm.BankConnection, connection["id"]).status, "revoked")


if __name__ == "__main__":
    unittest.main()
