from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.deps import get_current_user
from app.schemas.auth import RegisterRequest
from app.models.users import User
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

    # password hash
    hashed_password = hash_password(request.password)

    # user object
    new_user = User(
        email=request.email,
        password_hash=hashed_password
    )

    # write to database
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")
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
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid login payload")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Invalid login payload")
        email = data.get("email")
        password = data.get("password")

    else:
        form = await request.form()
        email = form.get("username")
        password = form.get("password")

    if not isinstance(email, str) or not isinstance(password, str) or not email or not password or len(password.encode()) > 72 or len(email) > 320:
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
