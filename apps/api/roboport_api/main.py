from fastapi import FastAPI, HTTPException

app = FastAPI(title="ROBOport development API", version="0.1.0")

@app.get("/health/live")
def live():
    return {"status": "alive"}

@app.get("/health/ready")
def ready():
    raise HTTPException(status_code=503, detail="Required dev services are not configured")

@app.get("/api/v1/meta")
def meta():
    return {"service": "ROBOport", "environment": "development"}

@app.get("/api/v1/catalog/brands")
def brands():
    return []

@app.get("/api/v1/catalog/models")
def models():
    return []

@app.get("/api/v1/catalog/models/{modelId}")
def model(modelId: str):
    raise HTTPException(status_code=404, detail="Model not found")
