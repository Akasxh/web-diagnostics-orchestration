from fastapi import FastAPI
from . import config  # noqa: F401  # reserved for future use
# from .models import DiagnosticsRequest, DiagnosticsResponse
# from .orchestrator import Orchestrator
import uvicorn


app = FastAPI(
    title="Web Diagnostics Orchestration",
    description="API for orchestrating web diagnostics (analytics, SEO, etc.).",
    version="0.1.0",
)


@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok"}



if __name__ == "__main__":

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)


