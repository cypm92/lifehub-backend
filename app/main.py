from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.database import SessionLocal, engine, Base, get_db
from app.init_db import init_db

# Crear tablas en la base de datos si no existen
Base.metadata.create_all(bind=engine)


# Gestión del ciclo de vida de la aplicación
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Se ejecuta al arrancar el servidor: Crear Admin inicial si no existe
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="LifeHub API",
    version="1.0.0",
    lifespan=lifespan
)

# Permitir peticiones desde el Frontend Vue
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# AUTH ENDPOINTS
# ---------------------------------------------------------

@app.post("/auth/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="El email ya está registrado")

    hashed_pw = security.get_password_hash(user.password)

    # Seguridad: todo registro externo entra automáticamente como 'guest'
    new_user = models.User(
        name=user.name,
        email=user.email,
        role="guest",
        hashed_password=hashed_pw
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@app.post("/auth/login", response_model=schemas.Token)
def login(credentials: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == credentials.email).first()
    if not user or not security.verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos"
        )

    access_token = security.create_access_token(data={"sub": str(user.id)})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@app.get("/auth/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(security.get_current_user)):
    """Obtiene los datos del usuario conectado mediante el Token"""
    return current_user


# ---------------------------------------------------------
# USERS ENDPOINTS (PROTEGIDOS)
# ---------------------------------------------------------

@app.get("/users", response_model=List[schemas.UserResponse])
def get_users(
        db: Session = Depends(get_db),
        current_user: models.User = Depends(security.get_current_user)
):
    """Devuelve el listado de usuarios para asignación en las vistas"""
    return db.query(models.User).all()


# ---------------------------------------------------------
# TRANSACTIONS ENDPOINTS (PROTEGIDOS)
# ---------------------------------------------------------

@app.get("/transactions", response_model=List[schemas.TransactionResponse])
def get_transactions(
        db: Session = Depends(get_db),
        current_user: models.User = Depends(security.get_current_user)
):
    return db.query(models.Transaction).all()


@app.post("/transactions", response_model=schemas.TransactionResponse)
def create_transaction(
        transaction: schemas.TransactionCreate,
        db: Session = Depends(get_db),
        current_user: models.User = Depends(security.get_current_user)
):
    new_trans = models.Transaction(**transaction.model_dump())
    db.add(new_trans)
    db.commit()
    db.refresh(new_trans)
    return new_trans