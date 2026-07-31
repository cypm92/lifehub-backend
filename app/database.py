from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def ensure_schema_compatibility() -> None:
    """Aplica cambios aditivos mínimos a instalaciones creadas antes de este modelo."""
    with engine.begin() as connection:
        def add_column_if_missing(table_name: str, column_name: str, definition: str) -> None:
            columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
            if column_name not in columns:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))

        add_column_if_missing("transactions", "week_number", "INTEGER")
        add_column_if_missing("fixed_expenses", "end_date", "DATE")
        add_column_if_missing("fixed_expenses", "installments_total", "INTEGER")
        add_column_if_missing("fixed_expenses", "installments_paid", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("fixed_expenses", "starts_month", "VARCHAR NOT NULL DEFAULT ''")
        add_column_if_missing("fixed_expenses", "group_id", "INTEGER")
        add_column_if_missing("fixed_expense_months", "counted_installment", "BOOLEAN NOT NULL DEFAULT FALSE")
        add_column_if_missing("weekly_expenses", "user_id", "INTEGER")
        add_column_if_missing("weekly_expenses", "weekly_category_id", "INTEGER")
        add_column_if_missing("weekly_expenses", "recharge_date", "DATE")
        add_column_if_missing("weekly_expenses", "is_manual_cycle", "BOOLEAN NOT NULL DEFAULT FALSE")
        add_column_if_missing("weekly_expenses", "is_excluded_cycle", "BOOLEAN NOT NULL DEFAULT FALSE")
        add_column_if_missing("weekly_expense_categories", "recharge_weekday", "INTEGER NOT NULL DEFAULT 4")
        add_column_if_missing("extra_expenses", "description", "VARCHAR NOT NULL DEFAULT ''")
        add_column_if_missing("extra_expenses", "status", "VARCHAR NOT NULL DEFAULT 'Pagado'")
        add_column_if_missing("monthly_budgets", "user_id", "INTEGER")
        add_column_if_missing("monthly_budgets", "is_closed", "BOOLEAN NOT NULL DEFAULT FALSE")
        add_column_if_missing("monthly_budgets", "is_created", "BOOLEAN NOT NULL DEFAULT FALSE")
        add_column_if_missing("monthly_budgets", "closed_at", "TIMESTAMP")
        connection.execute(text("UPDATE monthly_budgets SET user_id = (SELECT MIN(id) FROM users) WHERE user_id IS NULL"))
        connection.execute(text("""
            UPDATE fixed_expenses AS fixed
            SET starts_month = COALESCE(
                (SELECT MIN(entry.month_code) FROM fixed_expense_months AS entry
                 WHERE entry.fixed_expense_id = fixed.id AND entry.status = 'Pagado'),
                (SELECT MAX(entry.month_code) FROM fixed_expense_months AS entry
                 WHERE entry.fixed_expense_id = fixed.id),
                '0000-00'
            )
            WHERE fixed.starts_month = ''
        """))
        connection.execute(text("""
            UPDATE monthly_budgets AS budget
            SET is_created = TRUE
            WHERE EXISTS (
                SELECT 1 FROM transactions AS transaction
                WHERE transaction.user_id = budget.user_id
                  AND transaction.month_code = budget.month_code
            )
        """))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
