from pydantic import BaseModel

class LLMSettingsCreate(BaseModel):
    api_key: str
    model_name: str
    base_url: str

class LLMSettingsResponse(BaseModel):
    provider: str
    model_name: str
    base_url: str
    has_api_key:bool