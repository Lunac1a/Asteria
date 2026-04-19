from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from app.models.auth import RegisterRequest
from app.models.user import User
from app.db.session import get_db
from app.core.security import hash_password

router = APIRouter()

@router.post("/register")
def register(request: RegisterRequest, db: Session = Depends(get_db())):
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
    db.commit()
    db.refresh(new_user)

    # return result
    return {
        "id": new_user.id,
        "email": new_user.email
    }