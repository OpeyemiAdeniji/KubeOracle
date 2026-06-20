from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.routes import health, investigate, clusters, query
from core.config import settings

app = FastAPI(
    title="AI Kubernetes Agent",
    version="0.1.0",
    description="AI-powered Kubernetes troubleshooting agent",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["Health"])
app.include_router(investigate.router, tags=["Investigation"])
app.include_router(clusters.router, tags=["Clusters"])
app.include_router(query.router, tags=["Query"])


@app.on_event("startup")
async def on_startup():
    logger.info("AI Kubernetes Agent started")
    logger.info(f"Model: {settings.openrouter_model or 'not configured'}")
