from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.config import settings
from app.db.session import get_db
from app.models.users import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"}
)

def get_current_user_id(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> UUID:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception

        user_uuid = UUID(user_id)
        if db.get(User, user_uuid) is None:
            raise credentials_exception
        return user_uuid

    except (JWTError, ValueError, TypeError, AttributeError):
        raise credentials_exception


def get_current_user(
    db: Session = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
) -> User:

    user = db.query(User).filter(User.id == current_user_id).first()

    if user is None:
        raise credentials_exception

    return user
