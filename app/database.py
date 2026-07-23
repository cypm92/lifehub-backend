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
        add_column_if_missing("fixed_expenses", "group_id", "INTEGER")
        add_column_if_missing("fixed_expense_months", "counted_installment", "BOOLEAN NOT NULL DEFAULT FALSE")
        add_column_if_missing("weekly_expenses", "user_id", "INTEGER")
        add_column_if_missing("weekly_expenses", "weekly_category_id", "INTEGER")
        add_column_if_missing("extra_expenses", "description", "VARCHAR NOT NULL DEFAULT ''")
        add_column_if_missing("extra_expenses", "status", "VARCHAR NOT NULL DEFAULT 'Pagado'")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
