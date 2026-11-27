import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import banking_models as bm, models, security
from app.database import Base, get_db
from app.routers import banking, banking_assignments


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.db.add_all([models.User(id=1, name="Uno", email="a@example.com", hashed_password="x"),
                         models.User(id=2, name="Dos", email="b@example.com", hashed_password="x")])
        self.db.add_all([models.FixedExpense(id=1, user_id=1, concept="Casa", amount=20, due_day=1),
                         models.FixedExpense(id=2, user_id=2, concept="Otro", amount=30, due_day=1)])
        self.db.flush()
        self.db.add_all([models.FixedExpenseMonth(id=1, fixed_expense_id=1, month_code="2026-01", concept="Casa", amount=20, due_day=1, status="Esperando"),
                         models.FixedExpenseMonth(id=2, fixed_expense_id=2, month_code="2026-01", concept="Otro", amount=30, due_day=1, status="Esperando"),
                         models.WeeklyExpenseCategory(id=1, user_id=1, name="Comida"),
                         models.WeeklyExpenseCategory(id=2, user_id=2, name="Otra"),
                         models.ExtraExpenseCategory(id=1, user_id=1, name="Ocio"),
                         models.ExtraExpenseCategory(id=2, user_id=2, name="Otros")])
        for user_id in (1, 2):
            self.db.add(bm.BankConnection(id=user_id, user_id=user_id, bank_name="BBVA", bank_country="ES",
                                         session_encrypted="sensitive-encrypted-session", valid_until=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)))
        self.db.flush()
        self.db.add_all([bm.BankAccount(id=1, connection_id=1, provider_uid="a", data={}),
                         bm.BankAccount(id=2, connection_id=2, provider_uid="b", data={})])
        self.db.flush()
        raws = [
            {"credit_debit_indicator": "DBIT", "creditor": {"name": "ACME Energía"}, "remittance_information": ["Luz"]},
            {"credit_debit_indicator": "DBIT", "creditor": {"name": "acme energía"}, "remittance_information": ["Electricidad"]},
            {"credit_debit_indicator": "CRDT", "debtor": {"name": "Otro"}, "remittance_information": ["CAFÉ desayuno"]},
            {"credit_debit_indicator": "DBIT", "creditor": {"name": "Acme"}},
            {"credit_debit_indicator": "DBIT", "creditor": {"name": "Acme"}},
            {"credit_debit_indicator": "DBIT", "creditor": {"name": "Restaurante"}, "remittance_information": ["CAFÉ diario"]},
        ]
        self.db.add_all([bm.BankTransaction(id=i, account_id=2 if i == 4 else 1, dedupe_key=str(i), raw_data=raw)
                         for i, raw in enumerate(raws, 1)])
        self.db.commit()
        self.user = self.db.get(models.User, 1)
        self.app = FastAPI()
        self.app.include_router(banking.router)
        self.app.include_router(banking_assignments.router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[security.get_current_user] = lambda: self.user
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def assign(self, tx, kind, target=None, rule=None):
        return self.client.put(f"/banks/transactions/{tx}/assignment", json={"destination_type": kind,
                               "destination_id": target, "rule": rule})

    def test_ownership_destinations_and_no_budget_mutation(self):
        options = self.client.get("/banks/destinations").json()
        for kind in ("fixed", "weekly", "extra"):
            self.assertEqual([x["id"] for x in options[kind]], [1])
            self.assertEqual(self.assign(1, kind, 2).status_code, 400)
            self.assertEqual(self.assign(1, kind, 999).status_code, 400)
            self.assertEqual(self.assign(1, kind, None).status_code, 400)
            self.assertEqual(self.assign(1, kind, 1).status_code, 200)
        self.assertEqual(self.assign(1, "income", 1).status_code, 400)
        self.assertEqual(self.assign(1, "income").status_code, 200)
        self.assertEqual(self.assign(4, "income").status_code, 404)
        self.assertEqual(self.client.post("/banks/transactions/4/rules", json={"field": "concept", "contains": "luz"}).status_code, 404)
        self.assertEqual(self.db.query(bm.BankAssignment).count(), 1)
        self.assertEqual(self.db.query(models.Transaction).count(), 0)
        self.assertEqual(self.db.query(models.ExtraExpense).count(), 0)
        fixed = self.db.get(models.FixedExpenseMonth, 1)
        self.assertEqual(fixed.status, "Esperando")
        self.assertIsNone(fixed.paid_at)
        self.assertFalse(fixed.counted_installment)
        data = self.client.get("/banks/accounts/1/transactions").json()
        self.assertEqual(next(t for t in data["transactions"] if t["id"] == 1)["assignment"]["destination_type"], "income")
        self.assertNotIn("sensitive-encrypted-session", str(data))
        self.user = self.db.get(models.User, 2)
        self.assertEqual(self.client.delete("/banks/transactions/1/assignment").status_code, 404)
        self.assertEqual(self.client.get("/banks/accounts/1/transactions").status_code, 404)

    def test_rules_match_only_unassigned_and_revalidate_ownership(self):
        result = self.assign(1, "fixed", 1, {"field": "counterparty", "contains": " aCmE "})
        self.assertEqual(result.status_code, 200, result.text)
        rule_id = result.json()["rule"]["id"]
        self.assertEqual(result.json()["rule"]["contains"], "aCmE")
        self.assertEqual(self.assign(3, "weekly", 1).status_code, 200)
        response = self.client.post("/banks/transactions/3/rules", json={"field": "concept", "contains": "café"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.assign(5, "income").status_code, 200)
        # Create a second matching rule: earliest wins and existing assignments stay intact.
        self.assertEqual(self.client.post("/banks/transactions/3/rules", json={"field": "counterparty", "contains": "acme"}).status_code, 200)
        result = self.client.post("/banks/rules/apply")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {"assigned": 2, "invalid_rules": 0})
        assignment = self.db.query(bm.BankAssignment).filter_by(transaction_id=2).one()
        self.assertEqual((assignment.destination_type, assignment.destination_id), ("fixed", 1))
        concept_assignment = self.db.query(bm.BankAssignment).filter_by(transaction_id=6).one()
        self.assertEqual((concept_assignment.destination_type, concept_assignment.destination_id), ("weekly", 1))
        self.assertEqual(self.db.query(bm.BankAssignment).filter_by(transaction_id=5).one().destination_type, "income")
        self.assertIsNone(self.db.query(bm.BankAssignment).filter_by(transaction_id=4).first())
        self.assertEqual(self.client.post("/banks/rules/apply").json()["assigned"], 0)
        # A destination subsequently transferred/deleted invalidates the rule; it is never applied.
        self.db.get(models.FixedExpense, 1).user_id = 2
        self.db.commit()
        self.assertEqual(self.client.post("/banks/rules/apply").json()["invalid_rules"], 1)
        self.assertEqual(self.client.post("/banks/transactions/1/rules", json={"field": "concept", "contains": "luz"}).status_code, 400)
        self.user = self.db.get(models.User, 2)
        self.assertEqual(self.client.get("/banks/rules").json(), [])
        self.assertEqual(self.client.delete(f"/banks/rules/{rule_id}").status_code, 404)
        self.assertEqual(self.client.post("/banks/rules/apply").json()["assigned"], 0)
        self.assertEqual(self.db.query(models.Transaction).count(), 0)
        self.assertEqual(self.db.query(models.ExtraExpense).count(), 0)
        self.assertEqual(self.db.get(models.FixedExpenseMonth, 1).status, "Esperando")

    def test_rule_validation_and_manual_delete(self):
        self.assertEqual(self.client.post("/banks/transactions/1/rules", json={"field": "concept", "contains": "x"}).status_code, 400)
        self.assertEqual(self.assign(1, "income", rule={"field": "concept", "contains": "  "}).status_code, 422)
        self.assertEqual(self.assign(1, "unknown").status_code, 422)
        self.assertEqual(self.assign(1, "income").status_code, 200)
        rule = self.client.post("/banks/transactions/1/rules", json={"field": "concept", "contains": "Luz"}).json()
        self.assertEqual(self.client.delete(f"/banks/rules/{rule['id']}").status_code, 200)
        self.assertEqual(self.client.delete("/banks/transactions/1/assignment").status_code, 200)
        self.assertEqual(self.client.delete("/banks/transactions/1/assignment").status_code, 404)


if __name__ == "__main__":
    unittest.main()
