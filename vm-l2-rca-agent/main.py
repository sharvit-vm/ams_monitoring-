from fastapi import FastAPI

from config.settings import settings
from api.routes import router
from telemetry.middleware import TelemetryMiddleware

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION
)

app.add_middleware(TelemetryMiddleware)

app.include_router(
    router,
    prefix="/api/v1",
    tags=["L2 RCA Agent"]
)


@app.get("/")
@app.head("/")
def root():
    return {
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "Running"
    }


@app.get("/health")
@app.head("/health")
def health():
    return {"status": "ok", "service": settings.APP_NAME}