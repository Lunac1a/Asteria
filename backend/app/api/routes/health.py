from fastapi import APIRouter
from sqlalchemy import text
from app.db.session import SessionLocal

router = APIRouter()


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/db-health")
def db_health_check():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"database": "connected"}
    finally:
        db.close()