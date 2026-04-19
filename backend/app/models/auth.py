from pydantic import BaseModel, Field, EmailStr

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str