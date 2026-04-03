from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.routers import backtest
from app.routers import auth

app = FastAPI(title="Adaptive Options API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await init_db()


app.include_router(backtest.router, prefix="/api")
app.include_router(auth.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
