from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

app = FastAPI()


@app.get("/health/db")
def db_health(db: Session = Depends(get_db)) -> dict:
    result = db.execute(text("SELECT 1")).scalar()
    return {"database": "ok", "result": result}