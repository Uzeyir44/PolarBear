from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers import auth_router, users_router

app = FastAPI()
app.include_router(auth_router)
app.include_router(users_router)


@app.get("/health/db")
def db_health(db: Session = Depends(get_db)) -> dict:
    result = db.execute(text("SELECT 1")).scalar()
    return {"database": "ok", "result": result}