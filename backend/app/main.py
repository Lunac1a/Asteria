from fastapi import FastAPI
from app.api.router import api_router

app = FastAPI(
    title="Personal Knowledge Copilot API",
    version="0.1.0"
)

app.include_router(api_router)


@app.get("/")
def root():
    return {"message": "FastAPI backend is running"}