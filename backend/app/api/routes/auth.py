from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
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
def login(
        form_data: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email or password"
        )

    if not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email or password"
        )

    access_token = create_access_token(data={"sub": str(user.id)})

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

@router.get("/me", response_model=UserRead)
def read_me(current_user: User = Depends(get_current_user)):
    return current_user