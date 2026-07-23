from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Configuración con IP explícita 127.0.0.1 y credenciales/DB del proyecto LifeHub
DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:5432/lifehub_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()