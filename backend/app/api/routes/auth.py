from fastapi import APIRouter
from app.models.auth import RegisterRequest

router = APIRouter()

@router.post("/register")
def register(request: RegisterRequest):
    return {
        "email": request.email,
        "message": "register endpoint working"
    }
