from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.database import SessionLocal, engine, Base, ensure_schema_compatibility, get_db
from app.init_db import init_db
from app.routers import budgets, fixed_expenses, extra_expenses, savings
# Crear tablas en la base de datos si no existen
Base.metadata.create_all(bind=engine)
ensure_schema_compatibility()




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

# Registrar el router de presupuestos (que ya tiene prefix="/budgets")
app.include_router(budgets.router)

# Registrar el router de control semanal (que tiene prefix="/weekly-expenses")
app.include_router(budgets.weekly_router)
app.include_router(budgets.weekly_categories_router)
app.include_router(fixed_expenses.router)
app.include_router(fixed_expenses.groups_router)
app.include_router(extra_expenses.router)
app.include_router(extra_expenses.categories_router)
app.include_router(savings.router)

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


@app.get("/users", response_model=List[schemas.UserResponse])
def get_users(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    return db.query(models.User).all()


# ---------------------------------------------------------
# CATEGORIES
# ---------------------------------------------------------
@app.get("/categories", response_model=List[schemas.CategoryResponse])
def get_categories(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    return db.query(models.Category).all()


@app.post("/categories", response_model=schemas.CategoryResponse)
def create_category(category: schemas.CategoryCreate, db: Session = Depends(get_db),
                    current_user: models.User = Depends(security.get_current_user)):
    new_cat = models.Category(**category.model_dump())
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat


# ---------------------------------------------------------
# BUDGETS (PRESUPUESTOS MENSUALES)
# ---------------------------------------------------------
@app.get("/budgets/summary/{month_code}", response_model=schemas.MonthlyBudgetSummary)
def get_budget_summary(month_code: str, db: Session = Depends(get_db),
                       current_user: models.User = Depends(security.get_current_user)):
    """Obtiene el resumen detallado del presupuesto para un mes (ej. '2026-07')"""
    # 1. Obtener o crear el presupuesto del mes
    budget = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == month_code).first()
    if not budget:
        budget = models.MonthlyBudget(month_code=month_code, expected_income=0.0)
        db.add(budget)
        db.commit()
        db.refresh(budget)

    # 2. Cargar todas las categorías
    categories = db.query(models.Category).all()
    budget_items = {item.category_id: item.planned_amount for item in budget.items}

    fixed_planned_by_category = {}
    fixed_expenses = (
        db.query(models.FixedExpense)
        .filter(models.FixedExpense.user_id == current_user.id, models.FixedExpense.is_active.is_(True))
        .all()
    )
    for expense in fixed_expenses:
        if expense.category_id:
            fixed_planned_by_category[expense.category_id] = (
                fixed_planned_by_category.get(expense.category_id, 0.0) + expense.amount
            )

    for category_id, fixed_total in fixed_planned_by_category.items():
        budget_items[category_id] = max(budget_items.get(category_id, 0.0), fixed_total)

    # 3. Calcular gastos e ingresos reales
    transactions = db.query(models.Transaction).filter(models.Transaction.month_code == month_code).all()

    actual_spent_by_cat = {}
    actual_income = 0.0

    for t in transactions:
        if t.type == "ingreso":
            actual_income += t.amount
        else:
            if t.category_id:
                actual_spent_by_cat[t.category_id] = actual_spent_by_cat.get(t.category_id, 0.0) + t.amount

    # 4. Construir lista resumida por categoría
    paid_fixed_expenses = (
        db.query(models.FixedExpenseMonth, models.FixedExpense)
        .join(models.FixedExpense)
        .filter(
            models.FixedExpense.user_id == current_user.id,
            models.FixedExpenseMonth.month_code == month_code,
            models.FixedExpenseMonth.status == "Pagado",
        )
        .all()
    )
    for monthly_expense, expense in paid_fixed_expenses:
        if expense.category_id:
            actual_spent_by_cat[expense.category_id] = (
                actual_spent_by_cat.get(expense.category_id, 0.0) + monthly_expense.amount
            )

    items_summary = []
    total_planned_expense = 0.0
    total_actual_expense = 0.0

    for cat in categories:
        if cat.type == "ingreso":
            continue

        planned = budget_items.get(cat.id, 0.0)
        spent = actual_spent_by_cat.get(cat.id, 0.0)
        pct = (spent / planned * 100) if planned > 0 else (100.0 if spent > 0 else 0.0)

        total_planned_expense += planned
        total_actual_expense += spent

        items_summary.append({
            "category": cat,
            "planned_amount": planned,
            "actual_spent": spent,
            "percentage_used": round(pct, 1)
        })

    return {
        "month_code": month_code,
        "expected_income": budget.expected_income,
        "actual_income": actual_income,
        "total_planned_expense": total_planned_expense,
        "total_actual_expense": total_actual_expense,
        "items": items_summary
    }


@app.post("/budgets/set-income")
def set_expected_income(data: schemas.BudgetSetIncome, db: Session = Depends(get_db),
                        current_user: models.User = Depends(security.get_current_user)):
    budget = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == data.month_code).first()
    if not budget:
        budget = models.MonthlyBudget(month_code=data.month_code, expected_income=data.expected_income)
        db.add(budget)
    else:
        budget.expected_income = data.expected_income
    db.commit()
    return {"status": "ok"}


@app.post("/budgets/set-item")
def set_budget_item(data: schemas.BudgetItemCreate, month_code: str, db: Session = Depends(get_db),
                    current_user: models.User = Depends(security.get_current_user)):
    budget = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == month_code).first()
    if not budget:
        budget = models.MonthlyBudget(month_code=month_code, expected_income=0.0)
        db.add(budget)
        db.commit()
        db.refresh(budget)

    item = db.query(models.BudgetItem).filter(
        models.BudgetItem.monthly_budget_id == budget.id,
        models.BudgetItem.category_id == data.category_id
    ).first()

    if item:
        item.planned_amount = data.planned_amount
    else:
        item = models.BudgetItem(
            monthly_budget_id=budget.id,
            category_id=data.category_id,
            planned_amount=data.planned_amount
        )
        db.add(item)

    db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------
# TRANSACTIONS
# ---------------------------------------------------------
@app.get("/transactions/{month_code}", response_model=List[schemas.TransactionResponse])
def get_transactions_by_month(month_code: str, db: Session = Depends(get_db),
                              current_user: models.User = Depends(security.get_current_user)):
    return db.query(models.Transaction).filter(models.Transaction.month_code == month_code).all()


@app.post("/transactions", response_model=schemas.TransactionResponse)
def create_transaction(transaction: schemas.TransactionCreate, db: Session = Depends(get_db),
                       current_user: models.User = Depends(security.get_current_user)):
    new_trans = models.Transaction(**transaction.model_dump())
    db.add(new_trans)
    db.commit()
    db.refresh(new_trans)
    return new_trans


@app.put("/transactions/{transaction_id}", response_model=schemas.TransactionResponse)
def update_transaction(transaction_id: int, transaction: schemas.TransactionUpdate, db: Session = Depends(get_db),
                       current_user: models.User = Depends(security.get_current_user)):
    existing = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")
    existing.concept = transaction.concept
    existing.amount = transaction.amount
    db.commit()
    db.refresh(existing)
    return existing


@app.delete("/transactions/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db),
                       current_user: models.User = Depends(security.get_current_user)):
    existing = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")
    db.delete(existing)
    db.commit()

