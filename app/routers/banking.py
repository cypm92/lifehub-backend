"""Conexiones Enable Banking. Las asignaciones no afectan a los presupuestos."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import banking_models as bm, models, security
from app.config import settings
from app.database import get_db
from app.services.banking import EnableBanking
from app.services.banking_sync import coverage, sync_connection

router = APIRouter(prefix="/banks", tags=["banks"])


def configuration():
    """Return validated credentials internally; never expose failure details."""
    try:
        if not all((settings.enable_banking_app_id, settings.enable_banking_key_path,
                    settings.enable_banking_redirect_url, settings.enable_banking_encryption_key)):
            return None
        url = urlsplit(settings.enable_banking_redirect_url)
        if url.scheme not in ("http", "https") or not url.netloc or url.query or url.fragment or url.username or url.password:
            return None
        key = Path(settings.enable_banking_key_path).read_bytes()
        private = serialization.load_pem_private_key(key, password=None)
        if not isinstance(private, rsa.RSAPrivateKey):
            return None
        cipher = Fernet(settings.enable_banking_encryption_key.encode("ascii"))
        return EnableBanking(settings.enable_banking_app_id, key), cipher
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def configured():
    result = configuration()
    if result is None:
        raise HTTPException(503, "Banca no configurada")
    return result


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Missing timezone")
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def bank_allowed(name):
    return isinstance(name, str) and bool(re.search(r"\b(revolut|bbva|sabadell)\b", name, re.IGNORECASE))


def bank_list(provider):
    try:
        rows = provider.aspsps()["aspsps"]
        if not isinstance(rows, list):
            raise ValueError()
        return [row for row in rows if isinstance(row, dict) and bank_allowed(row.get("name"))
                and isinstance(row.get("country"), str) and re.fullmatch(r"[A-Z]{2}", row["country"])]
    except Exception:
        raise HTTPException(502, "No se pudo consultar el proveedor bancario") from None


def owned_connection(db, user_id, connection_id):
    connection = db.query(bm.BankConnection).filter_by(id=connection_id, user_id=user_id).first()
    if connection is None:
        raise HTTPException(404, "Conexión no encontrada")
    return connection


def public_connection(connection):
    status = connection.status
    if status == "revoked" or connection.valid_until <= utcnow():
        status = "expired"
    return {"id": connection.id, "bank_name": connection.bank_name,
            "bank_country": connection.bank_country, "valid_until": connection.valid_until.isoformat() + "Z",
            "status": status, "created_at": connection.created_at.isoformat() + "Z"}


class StartAuth(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")


class Callback(BaseModel):
    code: str | None = Field(default=None, max_length=4096)
    state: str | None = Field(default=None, max_length=512)
    return_url: str | None = Field(default=None, max_length=8192)
    error: str | None = Field(default=None, max_length=512)


def start_authorization(db, user_id, name, country, connection_id=None):
    provider, _ = configured()
    bank = next((b for b in bank_list(provider) if b["name"] == name and b["country"] == country), None)
    if bank is None:
        raise HTTPException(400, "Banco no disponible")
    state = secrets.token_urlsafe(32)
    now = utcnow()
    # maximum_consent_validity is expressed in seconds by the provider when present.
    max_seconds = bank.get("maximum_consent_validity")
    duration = min(90 * 86400, max_seconds) if isinstance(max_seconds, int) and not isinstance(max_seconds, bool) and max_seconds > 0 else 90 * 86400
    payload = {"access": {"valid_until": (now + timedelta(seconds=duration)).isoformat() + "Z"},
               "aspsp": {"name": name, "country": country}, "state": state,
               "redirect_url": settings.enable_banking_redirect_url, "psu_type": "personal"}
    try:
        url = provider.authorize(payload)["url"]
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError()
    except Exception:
        raise HTTPException(502, "No se pudo iniciar la autorización bancaria") from None
    db.add(bm.BankAuthorization(user_id=user_id, state_hash=hashlib.sha256(state.encode()).hexdigest(),
                                bank_name=name, bank_country=country, connection_id=connection_id,
                                expires_at=now + timedelta(minutes=15)))
    db.commit()
    return {"url": url}


@router.get("/status")
def status(user: models.User = Depends(security.get_current_user)):
    return {"configured": configuration() is not None}


@router.get("/aspsps")
def aspsps(user: models.User = Depends(security.get_current_user)):
    provider, _ = configured()
    return {"banks": [{"name": b["name"], "country": b["country"]} for b in bank_list(provider)]}


@router.post("/auth")
def start(payload: StartAuth, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    return start_authorization(db, user.id, payload.name, payload.country)


@router.post("/connections/{connection_id}/renew")
def renew(connection_id: int, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    connection = owned_connection(db, user.id, connection_id)
    return start_authorization(db, user.id, connection.bank_name, connection.bank_country, connection.id)


@router.post("/callback")
def callback(payload: Callback, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    provider, cipher = configured()
    code, state, error = payload.code, payload.state, payload.error
    if payload.return_url:
        if code or state or error:
            raise HTTPException(400, "Proporcione una URL o code y state, no ambos")
        actual, expected = urlsplit(payload.return_url), urlsplit(settings.enable_banking_redirect_url)
        if (actual.scheme, actual.netloc, actual.path, actual.fragment) != (expected.scheme, expected.netloc, expected.path, ""):
            raise HTTPException(400, "URL de retorno no válida")
        params = parse_qs(actual.query, keep_blank_values=True)
        if any(len(params.get(k, [])) > 1 for k in ("code", "state", "error")):
            raise HTTPException(400, "Parámetros de retorno duplicados")
        code, state, error = (params.get(k, [None])[0] for k in ("code", "state", "error"))
    if not state or len(state) > 512 or (not code and not error) or (code and error) or (code and len(code) > 4096):
        raise HTTPException(400, "Respuesta de autorización no válida")
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    now = utcnow()
    auth = db.query(bm.BankAuthorization).filter_by(user_id=user.id, state_hash=state_hash).first()
    if not auth:
        raise HTTPException(400, "State inválido o caducado")
    # Atomic claim prevents parallel callbacks from exchanging the same code twice.
    claimed = db.query(bm.BankAuthorization).filter(
        bm.BankAuthorization.id == auth.id, bm.BankAuthorization.consumed_at.is_(None),
        bm.BankAuthorization.expires_at > now).update({bm.BankAuthorization.consumed_at: now}, synchronize_session=False)
    db.commit()
    if claimed != 1:
        raise HTTPException(400, "State inválido o caducado")
    if error:
        raise HTTPException(400, "El banco rechazó la autorización")
    try:
        session = provider.create_session(code)
        session_id = session["session_id"]
        valid_until = parse_time(session["access"]["valid_until"])
        if not isinstance(session_id, str) or not session_id or not isinstance(session["accounts"], list):
            raise ValueError()
        aspsp = session.get("aspsp")
        if not isinstance(aspsp, dict) or aspsp.get("name") != auth.bank_name or aspsp.get("country") != auth.bank_country:
            raise ValueError()
        accounts = session["accounts"]
        if any(not isinstance(a, dict) or not isinstance(a.get("uid"), str) or not a["uid"] for a in accounts):
            raise ValueError()
        encrypted = cipher.encrypt(session_id.encode()).decode("ascii")
    except Exception:
        raise HTTPException(502, "No se pudo completar la autorización bancaria") from None
    connection = bm.BankConnection(user_id=user.id, bank_name=auth.bank_name, bank_country=auth.bank_country,
                                   session_encrypted=encrypted, valid_until=valid_until, status="active")
    db.add(connection)
    db.flush()
    seen = set()
    for account in accounts:
        if account["uid"] not in seen:
            db.add(bm.BankAccount(connection_id=connection.id, provider_uid=account["uid"], data=account))
            seen.add(account["uid"])
    db.commit()
    db.refresh(connection)
    # Keep the old record and its history. Revoke only after the replacement is saved.
    if auth.connection_id:
        old = db.query(bm.BankConnection).filter_by(id=auth.connection_id, user_id=user.id).first()
        if old and old.status != "revoked":
            try:
                provider.revoke(cipher.decrypt(old.session_encrypted.encode()).decode())
                old.status = "revoked"
                db.commit()
            except Exception:
                db.rollback()  # Old connection remains available for a later retry.
    return public_connection(connection)


@router.get("/connections")
def connections(db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    rows = db.query(bm.BankConnection).filter_by(user_id=user.id).order_by(bm.BankConnection.id.desc()).all()
    credentials = configuration()
    for connection in rows:
        if connection.status == "revoked" or connection.valid_until <= utcnow():
            continue
        if credentials is None:
            connection.status = "error"
            continue
        provider, cipher = credentials
        try:
            session_id = cipher.decrypt(connection.session_encrypted.encode()).decode()
            remote = provider.session(session_id)
            connection.valid_until = parse_time(remote["access"]["valid_until"])
            connection.status = "active" if remote.get("status") == "AUTHORIZED" else "error"
        except Exception:
            connection.status = "error"
    db.commit()
    return [public_connection(c) for c in rows]


@router.post("/connections/{connection_id}/sync")
def sync(connection_id: int, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    connection = db.query(bm.BankConnection).filter_by(id=connection_id, user_id=user.id).with_for_update().first()
    if connection is None:
        raise HTTPException(404, "Conexión no encontrada")
    if connection.status == "revoked" or connection.valid_until <= utcnow():
        raise HTTPException(409, "La conexión está caducada; renuévela antes de sincronizar")
    provider, cipher = configured()
    try:
        session_id = cipher.decrypt(connection.session_encrypted.encode()).decode()
        result = sync_connection(db, connection, provider, session_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        # No conservar cuentas, saldos o movimientos de una sincronización parcial.
        connection = db.query(bm.BankConnection).filter_by(id=connection_id, user_id=user.id).first()
        if connection is not None:
            connection.status = "error"
            db.commit()
        raise HTTPException(502, "No se pudo sincronizar la conexión bancaria") from None


@router.get("/connections/{connection_id}/accounts")
def accounts(connection_id: int, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    owned_connection(db, user.id, connection_id)
    rows = db.query(bm.BankAccount).filter_by(connection_id=connection_id).order_by(bm.BankAccount.id).all()
    return [{"id": a.id, "connection_id": a.connection_id, "data": a.data, "balances": a.balances} for a in rows]


@router.get("/accounts/{account_id}/transactions")
def transactions(account_id: int, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500),
                 db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    account = db.query(bm.BankAccount).join(bm.BankConnection).filter(
        bm.BankAccount.id == account_id, bm.BankConnection.user_id == user.id).first()
    if account is None:
        raise HTTPException(404, "Cuenta no encontrada")
    query = db.query(bm.BankTransaction).filter_by(account_id=account.id)
    total = query.count()
    rows = query.order_by(bm.BankTransaction.id.desc()).offset(offset).limit(limit).all()
    assignments = {a.transaction_id: a for a in db.query(bm.BankAssignment).filter(
        bm.BankAssignment.user_id == user.id, bm.BankAssignment.transaction_id.in_([tx.id for tx in rows])).all()}
    def public_assignment(tx):
        assignment = assignments.get(tx.id)
        if not assignment:
            return None
        return {"id": assignment.id, "transaction_id": tx.id, "destination_type": assignment.destination_type,
                "destination_id": assignment.destination_id}
    return {"total": total, "transactions": [{"id": tx.id, "account_id": tx.account_id,
                                                "raw_data": tx.raw_data, "assignment": public_assignment(tx)} for tx in rows]}


@router.get("/coverage")
def bank_coverage(db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    rows = db.query(bm.BankConnection, bm.BankAccount).outerjoin(
        bm.BankAccount, bm.BankAccount.connection_id == bm.BankConnection.id).filter(
        bm.BankConnection.user_id == user.id).order_by(bm.BankConnection.id, bm.BankAccount.id).all()
    account_ids = [account.id for _, account in rows if account is not None]
    raw_by_account = {account_id: [] for account_id in account_ids}
    if account_ids:
        for account_id, raw in db.query(bm.BankTransaction.account_id, bm.BankTransaction.raw_data).filter(
                bm.BankTransaction.account_id.in_(account_ids)).all():
            raw_by_account[account_id].append(raw)
    banks = {}
    for connection, account in rows:
        key = (connection.bank_name, connection.bank_country)
        bank = banks.setdefault(key, {"name": key[0], "country": key[1], "accounts": [], "raws": []})
        if account is not None:
            raws = raw_by_account[account.id]
            bank["raws"].extend(raws)
            bank["accounts"].append({"id": account.id, "connection_id": connection.id,
                                     "name": (account.data or {}).get("name"), **coverage(raws)})
    return {"banks": [{"name": bank["name"], "country": bank["country"], "accounts": bank["accounts"],
                       **coverage(bank["raws"])} for bank in banks.values()]}


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: int, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    connection = owned_connection(db, user.id, connection_id)
    if connection.status != "revoked":
        provider, cipher = configured()
        try:
            provider.revoke(cipher.decrypt(connection.session_encrypted.encode()).decode())
        except Exception:
            raise HTTPException(502, "No se pudo revocar la conexión; no se ha eliminado") from None
    account_ids = db.query(bm.BankAccount.id).filter_by(connection_id=connection.id)
    transaction_ids = db.query(bm.BankTransaction.id).filter(bm.BankTransaction.account_id.in_(account_ids))
    db.query(bm.BankAssignment).filter(bm.BankAssignment.transaction_id.in_(transaction_ids)).delete(synchronize_session=False)
    db.query(bm.BankTransaction).filter(bm.BankTransaction.account_id.in_(account_ids)).delete(synchronize_session=False)
    db.query(bm.BankAccount).filter_by(connection_id=connection.id).delete(synchronize_session=False)
    db.query(bm.BankAuthorization).filter_by(connection_id=connection.id).delete(synchronize_session=False)
    db.delete(connection)
    db.commit()
    return {"deleted": True}
