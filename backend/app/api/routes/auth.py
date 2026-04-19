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
async def login(
    request: Request,
    db: Session = Depends(get_db)
):
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
        email = data.get("email")
        password = data.get("password")

    else:
        form = await request.form()
        email = form.get("username")
        password = form.get("password")

    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Email and password required"
        )

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=400,
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