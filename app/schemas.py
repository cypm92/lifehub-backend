from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional
from app.models import UserRole, TransactionType

# --- ESQUEMAS DE USUARIO ---
class UserBase(BaseModel):
    name: str
    email: str
    role: UserRole = UserRole.EDITOR

class UserCreate(UserBase):
    pass

class UserResponse(UserBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

# --- ESQUEMAS DE TRANSACCIONES (Gastos/Ingresos) ---
class TransactionBase(BaseModel):
    concept: str
    amount: float
    type: TransactionType
    user_id: int

class TransactionCreate(TransactionBase):
    pass

class TransactionResponse(TransactionBase):
    id: int
    date: datetime

    class Config:
        from_attributes = True