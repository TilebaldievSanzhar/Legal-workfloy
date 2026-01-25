"""
Configuration management using pydantic-settings.
Loads environment variables from .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List, Optional
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Nextcloud Configuration
    nc_host: str = Field(..., description="Nextcloud server URL")
    nc_user: str = Field(..., description="Nextcloud username")
    nc_password: str = Field(..., description="Nextcloud password")
    target_folders: str = Field(
        ...,
        description="Comma-separated list of folders to monitor"
    )

    # Google Configuration
    google_credentials_path: str = Field(
        default="config/google_credentials.json",
        description="Path to Google service account credentials"
    )
    spreadsheet_id: str = Field(..., description="Google Sheets spreadsheet ID")

    # Gemini Configuration
    gemini_api_key: str = Field(..., description="Google Gemini API key")
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model to use"
    )

    # Tuning Parameters
    poll_interval: int = Field(
        default=600,
        description="Polling interval in seconds"
    )
    api_request_delay: float = Field(
        default=2.0,
        description="Delay between API requests in seconds"
    )
    batch_size: int = Field(
        default=5,
        description="Batch size for processing"
    )

    # State file
    state_file_path: str = Field(
        default="processed_state.json",
        description="Path to state file for tracking processed files"
    )

    # Year filter for batch processing (optional)
    process_years: Optional[str] = Field(
        default=None,
        description="Comma-separated list of years to process (e.g., '2024,2025'). If empty, process all years."
    )

    # Deduplication settings
    skip_duplicates: bool = Field(
        default=True,
        description="Skip files that already exist in Google Sheets by filename+subsidiary+year"
    )

    @property
    def target_folders_list(self) -> List[str]:
        """Parse comma-separated target folders into a list."""
        return [f.strip() for f in self.target_folders.split(",") if f.strip()]

    @property
    def process_years_list(self) -> Optional[List[int]]:
        """Parse comma-separated years into a list of integers.

        Returns:
            List of years to process, or None to process all years.
        """
        if not self.process_years:
            return None
        years = []
        for y in self.process_years.split(","):
            y = y.strip()
            if y.isdigit():
                years.append(int(y))
        return years if years else None

    @property
    def google_credentials_full_path(self) -> Path:
        """Get full path to Google credentials file."""
        return Path(__file__).parent / self.google_credentials_path.replace("config/", "")


# Singleton instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
