"""
Configuration and environment variable handling.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):

    # Add env variables here in this format.
    APP_ENV : str = Field(default="development", env="APP_ENV")
    GA4_PROPERTY_ID: str | None = Field(default=None, env="GA4_PROPERTY_ID")
    LITELLM_PROXY_URL: str | None = Field(default=None, env="LITELLM_PROXY_URL")
    SERVICE_ACCOUNT_MAIL : str | None = Field(default=None,env = "SERVICE_ACCOUNT_MAIL")
    SHEET_ID : str | None = Field(default=None,env = "SHEET_ID")
    GOOGLE_APPLICATION_CREDENTIALS: str = Field(default="credentials.json", alias="GOOGLE_APPLICATION_CREDENTIALS")
    AGENT_TAXONOMY_PATH : str = Field(default="agent_taxonomy.json", env="AGENT_TAXONOMY_PATH")
    LITELLM_KEY : str = Field(default=None,env = "LITELLM_KEY")


    class Config:
        env_file = ".env"
        extra = "ignore"



@lru_cache
def get_settings() -> Settings:
    return Settings()
