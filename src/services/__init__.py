"""Services package for external integrations."""

__all__ = ["NextcloudClient", "GoogleSheetsClient"]


def __getattr__(name: str):
    """Lazy import to avoid circular dependencies."""
    if name == "NextcloudClient":
        from .nextcloud_client import NextcloudClient
        return NextcloudClient
    if name == "GoogleSheetsClient":
        from .gsheets_client import GoogleSheetsClient
        return GoogleSheetsClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
