"""
Nextcloud WebDAV client for file operations.
Handles recursive folder scanning and file downloads.
"""

import hashlib
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import PurePosixPath
import tempfile

from webdav3.client import Client
from webdav3.exceptions import WebDavException

from ..models import FileMetadata
from ..utils import get_logger

logger = get_logger("nextcloud")


class NextcloudClient:
    """WebDAV client for Nextcloud operations."""

    def __init__(
            self,
            host: str,
            username: str,
            password: str
    ):
        """
        Initialize Nextcloud WebDAV client.
        """
        self.host = host.rstrip("/")
        self.username = username

        # Сохраняем префикс, который сервер добавляет к путям
        self.webdav_root = f"/remote.php/dav/files/{username}"

        # Configure WebDAV client
        webdav_options = {
            "webdav_hostname": f"{self.host}{self.webdav_root}/",  # Используем переменную
            "webdav_login": username,
            "webdav_password": password,
            "webdav_timeout": 30
        }

        self.client = Client(webdav_options)
        logger.info(f"Nextcloud client initialized for {self.host}")

    def scan_folder_recursive(
        self,
        folder_path: str,
        file_extension: str = ".pdf"
    ) -> Dict[str, FileMetadata]:
        """
        Recursively scan a folder and return all files with given extension.

        Args:
            folder_path: Path to folder to scan
            file_extension: File extension to filter (default: .pdf)

        Returns:
            Dictionary mapping file_id_hash to FileMetadata
        """
        result: Dict[str, FileMetadata] = {}
        self._scan_recursive(folder_path, file_extension, result)
        logger.info(f"Found {len(result)} {file_extension} files in {folder_path}")
        return result

    def _scan_recursive(
            self,
            folder_path: str,
            file_extension: str,
            result: Dict[str, FileMetadata]
    ) -> None:
        """
        Internal recursive scanning method.
        """
        try:
            # Normalize path
            folder_path = folder_path.rstrip("/")
            if not folder_path.startswith("/"):
                folder_path = "/" + folder_path

            # List folder contents
            items = self.client.list(folder_path, get_info=True)

            for item in items:
                # Получаем полный путь от сервера
                full_path = item.get("path", "")

                # Skip the parent directory entry (сравниваем с учетом возможного префикса)
                # Если full_path заканчивается на текущий folder_path, это сама папка
                if full_path.rstrip("/").endswith(folder_path):
                    continue

                # --- ВАЖНОЕ ИСПРАВЛЕНИЕ: Очищаем путь от префикса WebDAV ---
                relative_path = full_path
                if full_path.startswith(self.webdav_root):
                    relative_path = full_path[len(self.webdav_root):]

                # Обновляем путь в item, чтобы метаданные создавались корректно
                item["path"] = relative_path

                is_dir = item.get("isdir", False)

                if is_dir:
                    # Recurse into subdirectory используя корректный путь
                    self._scan_recursive(relative_path, file_extension, result)
                elif relative_path.lower().endswith(file_extension.lower()):
                    # Process file
                    file_meta = self._create_file_metadata(item)
                    result[file_meta.file_id] = file_meta

        except WebDavException as e:
            logger.error(f"Error scanning folder {folder_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error scanning {folder_path}: {e}")

    def _create_file_metadata(self, item: dict) -> FileMetadata:
        """
        Create FileMetadata from WebDAV item info.

        Args:
            item: WebDAV item dictionary

        Returns:
            FileMetadata instance
        """
        path = item.get("path", "")
        filename = PurePosixPath(path).name
        size = int(item.get("size", 0))
        etag = item.get("etag")

        # Parse modification time
        modified_str = item.get("modified")
        if modified_str:
            try:
                modified = datetime.strptime(
                    modified_str,
                    "%a, %d %b %Y %H:%M:%S %Z"
                )
            except ValueError:
                modified = datetime.now()
        else:
            modified = datetime.now()

        # Generate unique file ID hash
        file_id = self.generate_file_hash(path, etag or str(size))

        return FileMetadata(
            file_id=file_id,
            path=path,
            filename=filename,
            size=size,
            modified=modified,
            etag=etag
        )

    @staticmethod
    def generate_file_hash(path: str, etag: str = "") -> str:
        """
        Generate a unique hash for a file.

        Args:
            path: File path
            etag: Optional etag for uniqueness

        Returns:
            SHA256 hash string (first 16 characters)
        """
        content = f"{path}:{etag}".encode("utf-8")
        return hashlib.sha256(content).hexdigest()[:16]

    def download_file(self, remote_path: str) -> bytes:
        """
        Download a file from Nextcloud.

        Args:
            remote_path: Path to file in Nextcloud

        Returns:
            File contents as bytes
        """
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp_path = tmp.name

            # Download to temp file
            self.client.download_sync(remote_path, tmp_path)

            # Read contents
            with open(tmp_path, "rb") as f:
                content = f.read()

            # Clean up
            import os
            os.unlink(tmp_path)

            logger.debug(f"Downloaded {len(content)} bytes from {remote_path}")
            return content

        except WebDavException as e:
            logger.error(f"Error downloading {remote_path}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error downloading {remote_path}: {e}")
            raise

    def generate_public_link(self, path: str) -> str:
        """
        Generate a web UI link to file in Nextcloud.

        Uses the /apps/files/ endpoint which opens in the Nextcloud web interface
        and handles authentication via the normal login flow.

        Args:
            path: File path in Nextcloud

        Returns:
            URL to access the file in Nextcloud web UI
        """
        from urllib.parse import quote

        p = PurePosixPath(path)
        dir_path = str(p.parent)
        filename = p.name

        dir_encoded = quote(dir_path)
        filename_encoded = quote(filename)

        return f"{self.host.rstrip('/')}/apps/files/?dir={dir_encoded}&scrollto={filename_encoded}"

    def file_exists(self, path: str) -> bool:
        """
        Check if a file exists in Nextcloud.

        Args:
            path: Path to check

        Returns:
            True if file exists, False otherwise
        """
        try:
            return self.client.check(path)
        except WebDavException:
            return False

    def scan_multiple_folders(
        self,
        folder_paths: List[str],
        file_extension: str = ".pdf"
    ) -> Dict[str, FileMetadata]:
        """
        Scan multiple folders and return combined results.

        Args:
            folder_paths: List of folder paths to scan
            file_extension: File extension filter

        Returns:
            Combined dictionary of all files
        """
        all_files: Dict[str, FileMetadata] = {}

        for folder in folder_paths:
            logger.info(f"Scanning folder: {folder}")
            folder_files = self.scan_folder_recursive(folder, file_extension)
            all_files.update(folder_files)

        logger.info(f"Total files found across all folders: {len(all_files)}")
        return all_files
