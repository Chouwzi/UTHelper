from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os
from pathlib import Path

# Base directory for the application
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    """
    Application core settings using Pydantic Settings.
    Automatically loads from .env file or environment variables.
    """
    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # UTH Credentials
    UTH_USERNAME: str = Field(default="", description="Student ID")
    UTH_PASSWORD: str = Field(default="", description="Password")

    # UTH URLs
    MOODLE_BASE_URL: str = "https://courses.ut.edu.vn"
    MOODLE_LOGIN_URL: str = "https://courses.ut.edu.vn/login/index.php"
    
    # App Settings
    THEME: str = Field(default="system", description="UI Theme: system, dark, light")
    CHECK_INTERVAL_MINUTES: int = Field(default=60, description="Background check frequency")

# Instantiated globally for easy access
settings = Settings()
