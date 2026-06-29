from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, drivers, vehicles, pickup_requests, dropoff_requests

app = FastAPI(
    title="Employee Routing System — Backend",
    version="1.0.0",
    description="Admin manages drivers, vehicles and request approvals"
)

# Allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(drivers.router)
app.include_router(vehicles.router)
app.include_router(pickup_requests.router)
app.include_router(dropoff_requests.router)

@app.get("/", tags=["Health"])
def health():
    return {"status": "ok"}