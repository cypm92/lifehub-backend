from sqlalchemy import Boolean, Column, Date, Integer, String, Float, ForeignKey, DateTime, UniqueConstraint, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="guest")  # admin, editor, guest


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    icon = Column(String, default="📦")
    color = Column(String, default="#7c3aed")
    type = Column(String, nullable=False)  # 'gasto_fijo', 'gasto_semanal', 'gasto_extra', 'ingreso'


class MonthlyBudget(Base):
    __tablename__ = "monthly_budgets"
    __table_args__ = (UniqueConstraint("user_id", "month_code", name="uq_monthly_budget_user_month"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    month_code = Column(String, nullable=False, index=True)  # Formato: "YYYY-MM" (ej. "2026-07")
    expected_income = Column(Float, default=0.0)
    is_closed = Column(Boolean, default=False, nullable=False)
    is_created = Column(Boolean, default=False, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    items = relationship("BudgetItem", back_populates="monthly_budget", cascade="all, delete-orphan")
    user = relationship("User")


class BudgetItem(Base):
    __tablename__ = "budget_items"

    id = Column(Integer, primary_key=True, index=True)
    monthly_budget_id = Column(Integer, ForeignKey("monthly_budgets.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    planned_amount = Column(Float, default=0.0)

    monthly_budget = relationship("MonthlyBudget", back_populates="items")
    category = relationship("Category")


class MonthlyBudgetSnapshot(Base):
    __tablename__ = "monthly_budget_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    monthly_budget_id = Column(Integer, ForeignKey("monthly_budgets.id"), nullable=False, index=True)
    month_code = Column(String, nullable=False, index=True)
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    state = Column(JSON, nullable=False)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    concept = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False)  # 'gasto_fijo', 'gasto_semanal', 'gasto_extra', 'ingreso'
    month_code = Column(String, nullable=False, index=True)  # Formato: "YYYY-MM"
    date = Column(DateTime, default=datetime.utcnow)
    week_number = Column(Integer, nullable=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    category = relationship("Category")
    user = relationship("User")

class WeeklyExpense(Base):
    __tablename__ = "weekly_expenses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    weekly_category_id = Column(Integer, ForeignKey("weekly_expense_categories.id"), nullable=True, index=True)
    month_code = Column(String, index=True)  # Ej: "2026-07"
    week_number = Column(Integer)  # 1, 2, 3, 4, 5
    week_date_label = Column(String)  # Ej: "Viernes 03 Julio"
    recharge_date = Column(Date, nullable=True, index=True)
    is_manual_cycle = Column(Boolean, nullable=False, default=False)
    is_excluded_cycle = Column(Boolean, nullable=False, default=False)
    category_name = Column(String)  # Gasolina, Supermercado, Ocio
    allocated_amount = Column(Float, default=0.0)  # Presupuestado
    status = Column(String, default="Esperando")  # Pagado, Parado, Esperando


class WeeklyExpenseCategory(Base):
    __tablename__ = "weekly_expense_categories"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_weekly_expense_category_user_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    weekly_amount = Column(Float, nullable=False, default=0.0)
    recharge_weekday = Column(Integer, nullable=False, default=4)  # Lunes=0 ... Viernes=4
    is_active = Column(Boolean, nullable=False, default=True)

    user = relationship("User")
    expenses = relationship("WeeklyExpense")


class FixedExpense(Base):
    __tablename__ = "fixed_expenses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("fixed_expense_groups.id"), nullable=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    concept = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String, default="")
    due_day = Column(Integer, nullable=False)
    end_date = Column(Date, nullable=True)
    installments_total = Column(Integer, nullable=True)
    installments_paid = Column(Integer, default=0, nullable=False)
    starts_month = Column(String, nullable=False, default="")
    is_active = Column(Boolean, default=True, nullable=False)

    user = relationship("User")
    group = relationship("FixedExpenseGroup", back_populates="expenses")
    category = relationship("Category")
    monthly_entries = relationship("FixedExpenseMonth", back_populates="fixed_expense", cascade="all, delete-orphan")


class FixedExpenseGroup(Base):
    __tablename__ = "fixed_expense_groups"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_fixed_expense_group_user_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    color = Column(String, default="#7c3aed", nullable=False)

    user = relationship("User")
    expenses = relationship("FixedExpense", back_populates="group")


class FixedExpenseMonth(Base):
    __tablename__ = "fixed_expense_months"
    __table_args__ = (UniqueConstraint("fixed_expense_id", "month_code", name="uq_fixed_expense_month"),)

    id = Column(Integer, primary_key=True, index=True)
    fixed_expense_id = Column(Integer, ForeignKey("fixed_expenses.id"), nullable=False, index=True)
    month_code = Column(String, nullable=False, index=True)
    concept = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String, default="")
    due_day = Column(Integer, nullable=False)
    status = Column(String, default="Esperando", nullable=False)
    paid_at = Column(DateTime, nullable=True)
    counted_installment = Column(Boolean, default=False, nullable=False)

    fixed_expense = relationship("FixedExpense", back_populates="monthly_entries")


class ExtraExpenseCategory(Base):
    __tablename__ = "extra_expense_categories"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_extra_expense_category_user_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    color = Column(String, default="#7c3aed", nullable=False)

    user = relationship("User")
    expenses = relationship("ExtraExpense", back_populates="category")


class ExtraExpense(Base):
    __tablename__ = "extra_expenses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("extra_expense_categories.id"), nullable=True, index=True)
    month_code = Column(String, nullable=False, index=True)
    concept = Column(String, nullable=False)
    description = Column(String, default="", nullable=False)
    amount = Column(Float, nullable=False)
    expense_date = Column(Date, nullable=False)
    status = Column(String, default="Pagado", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")
    category = relationship("ExtraExpenseCategory", back_populates="expenses")


class SavingsSource(Base):
    __tablename__ = "savings_sources"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    source_type = Column(String, default="Banco", nullable=False)
    balance = Column(Float, default=0.0, nullable=False)

    user = relationship("User")
