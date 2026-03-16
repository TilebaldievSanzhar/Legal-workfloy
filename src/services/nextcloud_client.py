"""
Nextcloud WebDAV client for file operations.
Handles recursive folder scanning and file downloads.
"""

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import PurePosixPath
import tempfile

import requests
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
        self.password = password

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

            # Get Nextcloud file IDs for this folder
            fileids = self._get_fileids_for_folder(folder_path)

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
                    # Attach nc_fileid from PROPFIND result
                    item["nc_fileid"] = fileids.get(relative_path.rstrip("/"))
                    # Process file
                    file_meta = self._create_file_metadata(item)
                    result[file_meta.file_id] = file_meta

        except WebDavException as e:
            logger.error(f"Error scanning folder {folder_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error scanning {folder_path}: {e}")

    def _get_fileids_for_folder(self, folder_path: str) -> Dict[str, int]:
        """
        Get Nextcloud internal file IDs for all items in a folder via PROPFIND.

        Returns:
            Dict mapping relative path -> nc_fileid
        """
        from urllib.parse import unquote

        url = f"{self.host}{self.webdav_root}{folder_path}"
        body = """<?xml version="1.0" encoding="UTF-8"?>
        <d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:prop>
                <oc:fileid/>
            </d:prop>
        </d:propfind>"""

        try:
            resp = requests.request(
                "PROPFIND",
                url,
                data=body,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                auth=(self.username, self.password),
                timeout=30
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"PROPFIND failed for {folder_path}: {e}")
            return {}

        fileids: Dict[str, int] = {}
        try:
            root = ET.fromstring(resp.content)
            ns = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
            for response in root.findall("d:response", ns):
                href = response.findtext("d:href", "", ns)
                fileid_el = response.find(".//oc:fileid", ns)
                if href and fileid_el is not None and fileid_el.text:
                    # href is like /remote.php/dav/files/user/path
                    decoded = unquote(href)
                    if decoded.startswith(self.webdav_root):
                        rel_path = decoded[len(self.webdav_root):]
                    else:
                        rel_path = decoded
                    fileids[rel_path.rstrip("/")] = int(fileid_el.text)
        except ET.ParseError as e:
            logger.warning(f"Failed to parse PROPFIND response: {e}")

        return fileids

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

        nc_fileid = item.get("nc_fileid")

        return FileMetadata(
            file_id=file_id,
            path=path,
            filename=filename,
            size=size,
            modified=modified,
            etag=etag,
            nc_fileid=nc_fileid
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

    def generate_public_link(self, path: str, nc_fileid: Optional[int] = None) -> str:
        """
        Generate a web UI link to file in Nextcloud.

        If nc_fileid is available, uses /f/{fileid} which opens the file
        directly in the Nextcloud viewer. Otherwise falls back to directory link.

        Args:
            path: File path in Nextcloud
            nc_fileid: Nextcloud internal file ID (opens file directly)

        Returns:
            URL to access the file in Nextcloud web UI
        """
        if nc_fileid:
            return f"{self.host}/f/{nc_fileid}"

        from urllib.parse import quote

        p = PurePosixPath(path)
        dir_path = str(p.parent)
        filename = p.name

        dir_encoded = quote(dir_path)
        filename_encoded = quote(filename)

        return f"{self.host}/apps/files/?dir={dir_encoded}&scrollto={filename_encoded}"

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
