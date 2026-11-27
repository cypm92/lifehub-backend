import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import banking_models as bm, models, security
from app.database import Base, get_db
from app.routers import banking


class MovementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.db.add_all([models.User(id=i, name=str(i), email=f'{i}@example.com', hashed_password='x') for i in (1, 2)])
        self.db.add_all([bm.BankConnection(id=i, user_id=1 if i < 3 else 2, bank_name=f'Banco {i}', bank_country='ES',
                                         session_encrypted='secret-session', valid_until=datetime.now() + timedelta(days=1)) for i in (1, 2, 3)])
        self.db.add_all([
            bm.BankAccount(id=1, connection_id=1, provider_uid='a', data={'name': 'María López', 'cash_account_type': 'CACC'},
                           balances={'balances': [{'balance_type': 'ITAV', 'balance_amount': {'amount': '123.45', 'currency': 'EUR'}}]}),
            bm.BankAccount(id=2, connection_id=2, provider_uid='b', data={'name': 'Maria Lopez', 'account_id': {'iban': 'ES12 3456'}}),
            bm.BankAccount(id=3, connection_id=3, provider_uid='c', data={'name': 'Otro', 'account_id': {'iban': 'ES99'}})])
        today = datetime.now(timezone.utc).date()
        def raw(day, direction='DBIT', **extra):
            return {'booking_date': day.isoformat(), 'transaction_amount': {'amount': '12.50', 'currency': 'EUR'},
                    'credit_debit_indicator': direction, **extra}
        raws = [raw(today, creditor={'name': 'MARIA LOPEZ'}, remittance_information=[' Uno ', 'dos']),
                raw(today - timedelta(days=1), 'CRDT', debtor={'name': 'Empresa'}),
                {'booking_date': 'invalid', 'value_date': (today - timedelta(days=2)).isoformat(),
                 'transaction_amount': {'amount': '-3', 'currency': 'EUR'}, 'credit_debit_indicator': 'DBIT',
                 'creditor_account': {'iban': 'es123456'}, 'remittance_information': ['Traspaso']},
                raw(today - timedelta(days=40), creditor={'name': 'Comercio'}),
                raw(today, creditor_account={'iban': 'ES99'}),
                raw(today, creditor={'name': 'Privado'})]
        self.db.add_all([bm.BankTransaction(id=i, account_id=3 if i == 6 else 2 if i == 4 else 1,
                                            dedupe_key=f'secret-key-{i}', raw_data=value) for i, value in enumerate(raws, 1)])
        self.db.add(bm.BankAssignment(user_id=1, transaction_id=2, destination_type='income'))
        self.db.commit()
        self.user = self.db.get(models.User, 1)
        app = FastAPI()
        app.include_router(banking.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[security.get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_refunds_ignore_holder_name_but_can_match_own_account(self):
        today = datetime.now(timezone.utc).date().isoformat()
        def add(tx_id, account_id, amount, direction, concept, party):
            self.db.add(bm.BankTransaction(id=tx_id, account_id=account_id, dedupe_key=str(tx_id), raw_data={
                'booking_date': today, 'transaction_amount': {'amount': amount, 'currency': 'EUR'},
                'credit_debit_indicator': direction, 'remittance_information': [concept],
                ('debtor' if direction == 'CRDT' else 'creditor'): {'name': party}}))
        add(30, 1, '49.99', 'CRDT', 'Refund from Lidl', 'MARIA LOPEZ')
        add(31, 1, '29.99', 'CRDT', 'Refund from Tienda', 'MARIA LOPEZ')
        add(32, 1, '21', 'CRDT', 'Devolución compra', 'MARIA LOPEZ')
        add(33, 1, '22', 'CRDT', 'devolucion compra', 'MARIA LOPEZ')
        add(34, 1, '23', 'CRDT', 'Reembolso pedido', 'MARIA LOPEZ')
        add(35, 1, '24', 'CRDT', 'Refund from Tienda', 'MARIA LOPEZ')
        add(36, 2, '24', 'DBIT', 'Otra cuenta', 'Tienda')
        self.db.commit()
        rows = {row['id']: row for row in self.client.get('/banks/movements').json()['movements']}
        for tx_id in (30, 31, 32, 33, 34):
            self.assertFalse(rows[tx_id]['is_internal_transfer'], tx_id)
        self.assertTrue(rows[35]['is_internal_transfer'])
        self.assertTrue(rows[36]['is_internal_transfer'])

    def test_revolut_transfer_heuristics_and_account_labels(self):
        today = datetime.now(timezone.utc).date()
        self.db.get(bm.BankAccount, 1).data = {'name': 'Titular', 'cash_account_type': 'CACC', 'currency': 'EUR'}
        self.db.get(bm.BankAccount, 2).data = {'name': 'Titular', 'details': 'Supermercado', 'cash_account_type': 'SVGS', 'currency': 'EUR'}
        def add(tx_id, account, amount, direction, concept, delta=0):
            self.db.add(bm.BankTransaction(id=tx_id, account_id=account, dedupe_key=str(tx_id), raw_data={
                'booking_date': (today + timedelta(days=delta)).isoformat(),
                'transaction_amount': {'amount': amount, 'currency': 'EUR'},
                'credit_debit_indicator': direction, 'remittance_information': [concept]}))
        add(10, 1, '25', 'CRDT', 'Movimiento sin referencia')
        add(11, 2, '25', 'DBIT', 'Movimiento sin referencia', -1)
        add(12, 2, '25', 'DBIT', 'Movimiento sin referencia', -1)  # no puede reutilizar 10
        add(13, 1, '31', 'DBIT', 'To EUR MB:anonimo')
        add(14, 1, '32', 'DBIT', 'To EUR Créditos')
        add(15, 1, '33', 'CRDT', 'From Cuenta Remunerada')
        add(16, 1, '34', 'DBIT', 'To Supermercado')
        add(17, 1, '35', 'CRDT', 'From Beatriz M')
        add(18, 1, '36', 'DBIT', 'Openai *chatgpt Subscr')
        add(19, 1, '37', 'DBIT', 'To Cuenta Remunerada')
        add(20, 3, '35', 'DBIT', 'Otro usuario')
        add(21, 1, '38', 'DBIT', 'To EUR')
        add(22, 1, '39', 'CRDT', 'From Supermercado')
        self.db.add(bm.BankTransaction(id=23, account_id=1, dedupe_key='23', raw_data={
            'booking_date': today.isoformat(), 'transaction_amount': {'amount': '40', 'currency': 'EUR'},
            'credit_debit_indicator': 'CRDT', 'debtor': {'name': 'Cuenta Remunerada'}}))
        self.db.commit()
        result = self.client.get('/banks/movements?days=30').json()
        rows = {row['id']: row for row in result['movements']}
        for tx_id in (10, 11, 13, 14, 15, 16, 19, 21, 22, 23):
            self.assertTrue(rows[tx_id]['is_internal_transfer'], tx_id)
        for tx_id in (12, 17, 18):
            self.assertFalse(rows[tx_id]['is_internal_transfer'], tx_id)
        self.assertEqual(rows[10]['account_name'], 'Cuenta corriente')
        self.assertEqual(rows[11]['account_name'], 'Supermercado')
        self.assertEqual([a['name'] for a in result['accounts']], ['Cuenta corriente', 'Supermercado'])
        filtered = self.client.get('/banks/movements?connection_id=1').json()
        self.assertTrue(next(row for row in filtered['movements'] if row['id'] == 10)['is_internal_transfer'])
        self.db.get(bm.BankAccount, 2).data = {'name': 'Titular', 'product': 'Producto', 'cash_account_type': 'SVGS'}
        self.db.commit()
        self.assertEqual(self.client.get('/banks/movements?connection_id=2').json()['accounts'][0]['name'], 'Producto')

    def test_normalization_filter_order_and_heuristic(self):
        response = self.client.get('/banks/movements?days=30')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        rows = result['movements']
        self.assertEqual([r['id'] for r in rows], [5, 1, 2, 3])
        by_id = {r['id']: r for r in rows}
        self.assertEqual(by_id[1]['amount'], -12.5)
        self.assertEqual(by_id[1]['concept'], 'Uno dos')
        self.assertEqual(by_id[2]['amount'], 12.5)
        self.assertEqual(by_id[2]['counterparty'], 'Empresa')
        self.assertEqual(by_id[2]['assignment']['destination_type'], 'income')
        self.assertEqual(by_id[3]['amount'], -3)
        self.assertEqual(by_id[3]['counterparty'], 'Traspaso')
        self.assertTrue(by_id[1]['is_internal_transfer'])
        self.assertTrue(by_id[3]['is_internal_transfer'])
        self.assertFalse(by_id[5]['is_internal_transfer'])  # IBAN de otro usuario
        self.assertEqual(result['accounts'][0]['balance'], {'amount': 123.45, 'currency': 'EUR'})
        self.assertIsNone(result['accounts'][1]['balance'])
        self.assertNotIn('session_encrypted', response.text)
        self.assertNotIn('dedupe_key', response.text)
        self.assertNotIn('secret-', response.text)
        self.assertEqual([r['id'] for r in self.client.get('/banks/movements?connection_id=2&days=90').json()['movements']], [4])
        self.assertEqual(self.client.get('/banks/movements?connection_id=3').status_code, 404)
        self.assertEqual(self.client.get('/banks/movements?days=0').status_code, 422)
        self.user = self.db.get(models.User, 2)
        self.assertEqual([r['id'] for r in self.client.get('/banks/movements').json()['movements']], [6])
        self.assertEqual(self.client.get('/banks/movements?connection_id=1').status_code, 404)


if __name__ == '__main__':
    unittest.main()
