from sqlalchemy.orm import DeclarativeBase

from app.models.user import User
from app.models.user_llm_setting import UserLLMSetting

class Base(DeclarativeBase):
    pass