from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"}
)

def get_current_user_email(token: str = Depends(oauth2_scheme)) -> str:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )

        email: str | None = payload.get("sub")

        if email is None:
            raise credentials_exception

        return email

    except JWTError:
        raise credentials_exception

def get_current_user(
        db: Session = Depends(get_db),
        current_user_email: str = Depends(get_current_user_email)
) -> User:
    user = db.query(User).filter(User.email == current_user_email).first()

    if user is None:
        raise credentials_exception

    return user