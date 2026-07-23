from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

# --- SCHEMAS DE USUARIO ---
class UserBase(BaseModel):
    name: str
    email: EmailStr
    role: str = "editor"

class UserCreate(UserBase):
    password: str  # <-- Requerido al crear usuario

class UserResponse(UserBase):
    id: int

    class Config:
        from_attributes = True


# --- SCHEMAS DE AUTENTICACIÓN ---
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# --- SCHEMAS DE TRANSACCIONES ---
class TransactionBase(BaseModel):
    concept: str
    amount: float
    type: str

class TransactionCreate(TransactionBase):
    user_id: int

class TransactionResponse(TransactionBase):
    id: int
    user_id: int
    created_at: datetime

    class Config:
        from_attributes = True