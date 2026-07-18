from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.aiboard import router as aiboard_router
from app.api.me import router as me_router
from app.api.meetings import router as meetings_router
from app.api.minutes import router as minutes_router
from app.api.notion import router as notion_router
from app.api.slack import router as slack_router
from app.api.tasks import router as tasks_router
from app.api.teams import router as teams_router
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
app.include_router(me_router, prefix="/api", tags=["me"])
app.include_router(meetings_router, prefix="/api", tags=["meetings"])
app.include_router(notion_router, prefix="/api", tags=["notion"])
app.include_router(slack_router, prefix="/api", tags=["slack"])
app.include_router(tasks_router, prefix="/api", tags=["tasks"])
app.include_router(teams_router, prefix="/api", tags=["teams"])
app.include_router(aiboard_router, prefix="/api/external/aiboard", tags=["aiboard"])


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
