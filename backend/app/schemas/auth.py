from pydantic import BaseModel, Field, EmailStr, field_validator

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must fit within 72 UTF-8 bytes")
        return value

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
