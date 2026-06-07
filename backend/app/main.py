from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.minutes import router as minutes_router
from app.core.config import get_settings


settings = get_settings()

app = FastAPI(title=settings.app_name)

if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(minutes_router, prefix="/api/minutes", tags=["minutes"])


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
