from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from app.api import geo, auth, membership, oauth, contact
from app.utils.error_handler import global_exception_handler

app = FastAPI(
    title="GEO Readiness Checker API",
    description="API for checking website readiness for Generative Engine Optimization (GEO)",
    version="0.1.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加全局异常处理器
app.add_exception_handler(Exception, global_exception_handler)
app.add_exception_handler(RequestValidationError, global_exception_handler)

# Include API routes
app.include_router(geo.router, prefix="/api", tags=["geo"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(membership.router, prefix="/api", tags=["membership"])
app.include_router(oauth.router, prefix="/api/oauth", tags=["oauth"])
app.include_router(contact.router, prefix="/api", tags=["contact"])

@app.get("/")
async def root():
    return {"message": "GEO Readiness Checker API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
