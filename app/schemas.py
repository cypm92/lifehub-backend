from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import date, datetime


# Auth & User
class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: Optional[str] = "guest"


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


# Categories
class CategoryCreate(BaseModel):
    name: str
    icon: str
    color: str
    type: str


class CategoryResponse(CategoryCreate):
    id: int

    class Config:
        from_attributes = True


# Budget Items & Monthly Budget
class BudgetItemCreate(BaseModel):
    category_id: int
    planned_amount: float


class BudgetItemResponse(BaseModel):
    id: int
    category_id: int
    planned_amount: float
    category: CategoryResponse

    class Config:
        from_attributes = True


class BudgetSetIncome(BaseModel):
    month_code: str
    expected_income: float


class BudgetCloneRequest(BaseModel):
    from_month: str  # Ej: "2026-06"
    to_month: str    # Ej: "2026-07"


# Transactions
class TransactionCreate(BaseModel):
    concept: str
    amount: float
    type: str
    month_code: str
    category_id: Optional[int] = None
    user_id: Optional[int] = None
    week_number: Optional[int] = None


class TransactionUpdate(BaseModel):
    concept: str
    amount: float


class TransactionResponse(TransactionCreate):
    id: int
    date: datetime
    category: Optional[CategoryResponse] = None
    user: Optional[UserResponse] = None

    class Config:
        from_attributes = True


# Resumen Completo del Mes
class CategoryBudgetSummary(BaseModel):
    category: CategoryResponse
    planned_amount: float
    actual_spent: float
    percentage_used: float


class MonthlyBudgetSummary(BaseModel):
    month_code: str
    expected_income: float
    actual_income: float
    total_planned_expense: float
    total_actual_expense: float
    items: List[CategoryBudgetSummary]

class WeeklyExpenseBase(BaseModel):
    month_code: str
    week_number: int
    week_date_label: str
    category_name: str
    allocated_amount: float
    status: str
    recharge_date: Optional[date] = None

class WeeklyExpenseCreate(WeeklyExpenseBase):
    pass

class WeeklyExpenseResponse(WeeklyExpenseBase):
    id: int

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr


class WeeklyExpenseCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    weekly_amount: float = Field(ge=0)
    recharge_weekday: int = Field(default=4, ge=0, le=6)


class WeeklyExpenseCategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    weekly_amount: Optional[float] = Field(default=None, ge=0)
    recharge_weekday: Optional[int] = Field(default=None, ge=0, le=6)


class WeeklyExpenseCategoryResponse(WeeklyExpenseCategoryCreate):
    id: int

    class Config:
        from_attributes = True


class FixedExpenseCreate(BaseModel):
    concept: str = Field(min_length=1, max_length=120)
    amount: float = Field(gt=0)
    description: str = Field(default="", max_length=500)
    due_day: int = Field(ge=1, le=31)
    category_id: Optional[int] = None
    group_id: Optional[int] = None
    end_date: Optional[date] = None
    installments_total: Optional[int] = Field(default=None, gt=0)
    installments_paid: int = Field(default=0, ge=0)


class FixedExpenseUpdate(BaseModel):
    concept: Optional[str] = Field(default=None, min_length=1, max_length=120)
    amount: Optional[float] = Field(default=None, gt=0)
    description: Optional[str] = Field(default=None, max_length=500)
    due_day: Optional[int] = Field(default=None, ge=1, le=31)
    category_id: Optional[int] = None
    group_id: Optional[int] = None
    end_date: Optional[date] = None
    installments_total: Optional[int] = Field(default=None, gt=0)
    installments_paid: Optional[int] = Field(default=None, ge=0)


class FixedExpenseMonthUpdate(BaseModel):
    status: str = Field(pattern="^(Esperando|Pagado)$")


class FixedExpenseMonthResponse(BaseModel):
    id: int
    fixed_expense_id: int
    month_code: str
    concept: str
    amount: float
    description: str
    due_day: int
    due_date: str
    status: str
    paid_at: Optional[datetime] = None
    end_date: Optional[date] = None
    installments_total: Optional[int] = None
    installments_paid: int = 0
    installments_pending: Optional[int] = None
    installments_percentage: Optional[float] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    group_color: Optional[str] = None

    class Config:
        from_attributes = True


class FixedExpenseGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#7c3aed", pattern="^#[0-9A-Fa-f]{6}$")


class FixedExpenseGroupUpdate(FixedExpenseGroupCreate):
    pass


class FixedExpenseGroupResponse(FixedExpenseGroupCreate):
    id: int

    class Config:
        from_attributes = True


class ExtraExpenseCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#7c3aed", pattern="^#[0-9A-Fa-f]{6}$")


class ExtraExpenseCategoryUpdate(ExtraExpenseCategoryCreate):
    pass


class ExtraExpenseCategoryResponse(ExtraExpenseCategoryCreate):
    id: int

    class Config:
        from_attributes = True


class ExtraExpenseCreate(BaseModel):
    concept: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    amount: float = Field(gt=0)
    expense_date: date
    category_id: Optional[int] = None


class ExtraExpenseUpdate(ExtraExpenseCreate):
    status: Optional[str] = Field(default=None, pattern="^(Pagado|Pendiente)$")


class ExtraExpenseStatusUpdate(BaseModel):
    status: str = Field(pattern="^(Pagado|Pendiente)$")


class ExtraExpenseResponse(BaseModel):
    id: int
    month_code: str
    concept: str
    description: str
    amount: float
    expense_date: date
    status: str
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    category_color: Optional[str] = None


class SavingsSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source_type: str = Field(default="Banco", max_length=40)
    balance: float = Field(default=0, ge=0)


class SavingsSourceResponse(SavingsSourceCreate):
    id: int

    class Config:
        from_attributes = True
