from fastapi import FastAPI

from api.routes import router
from telemetry.middleware import RequestIDMiddleware


app = FastAPI(
    title="DB Fix Agent",
    version="1.0"
)

app.add_middleware(RequestIDMiddleware)
app.include_router(router)


@app.get("/")
def root():
    return {"status": "ok", "message": "DB Fix Agent is running", "docs": "/docs"}