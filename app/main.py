from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.redis_config import redis_client
from app.health import router as health_router
from app.auth import router as auth_router
from app.calendar import router as calendar_router
from app.chat import router as chat_router
import os

app = FastAPI()

# CORS configuration
frontend_url = os.getenv("FRONTEND_URL", "https://google-calendar-bot-lb7lm5oq.devinapps.com")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/auth")
app.include_router(calendar_router, prefix="/calendar")
app.include_router(chat_router, prefix="/chat")

@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Calendar API is running"
    }
