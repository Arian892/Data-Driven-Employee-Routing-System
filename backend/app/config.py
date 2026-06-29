from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    jwt_secret: str = Field(validation_alias=AliasChoices("jwt_secret", "jwt_secret_key"))
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(
        default=480,
        validation_alias=AliasChoices("jwt_expire_minutes", "access_token_expire_minutes"),
    )

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

settings = Settings()
