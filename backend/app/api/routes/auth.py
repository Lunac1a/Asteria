from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.models import user
from app.models.auth import RegisterRequest, LoginRequest
from app.models.user import User
from app.db.session import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.schemas.user import UserRead

router = APIRouter()

@router.post("/register")
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    # check email exist
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    print("raw password:", request.password)
    print("password length:", len(request.password.encode("utf-8")))

    # password hash
    hashed_password = hash_password(request.password)

    # user object
    new_user = User(
        email=request.email,
        password_hash=hashed_password
    )

    # write to database
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # return result
    return {
        "id": new_user.id,
        "email": new_user.email
    }

@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid email or password")

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid email or password")

    access_token = create_access_token(data={"sub": str(user.id)})

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

@router.get("/me", response_model=UserRead)
def read_me(current_user: User = Depends(get_current_user)):
    return current_user