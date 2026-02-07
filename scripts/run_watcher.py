#!/usr/bin/env python3
"""
Watcher script for monitoring Nextcloud folders.
Detects new and deleted files, processes them accordingly.
"""

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings
from src.models import FileMetadata, TableRow
from src.llm_engine import LLMEngine
from src.path_parser import PathInfo, extract_year, get_folder_from_path
from src.services.nextcloud_client import NextcloudClient
from src.services.gsheets_client import GoogleSheetsClient
from src.utils import setup_logging, get_logger


def load_state(state_path: str) -> Dict[str, dict]:
    """
    Load processed files state from JSON file.

    Args:
        state_path: Path to state file

    Returns:
        Dictionary of file_id -> metadata
    """
    path = Path(state_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state_path: str, state: Dict[str, dict]) -> None:
    """
    Save processed files state to JSON file.

    Args:
        state_path: Path to state file
        state: State dictionary to save
    """
    path = Path(state_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def process_new_file(
    file_meta: FileMetadata,
    nextcloud: NextcloudClient,
    llm: LLMEngine,
    gsheets: GoogleSheetsClient,
    target_folders: List[str]
) -> bool:
    """
    Process a new file: download, extract data, save to sheets.

    Args:
        file_meta: File metadata
        nextcloud: Nextcloud client
        llm: LLM engine
        gsheets: Google Sheets client
        target_folders: List of target folders

    Returns:
        True if successful, False otherwise
    """
    logger = get_logger("watcher")

    try:
        # Download file
        logger.info(f"Downloading: {file_meta.filename}")
        pdf_bytes = nextcloud.download_file(file_meta.path)

        # Extract data with LLM
        logger.info(f"Extracting data from: {file_meta.filename}")
        extract = llm.extract_with_fallback(pdf_bytes, file_meta.filename)

        # Parse path for metadata
        path_info = PathInfo(file_meta.path, target_folders)

        # Generate Nextcloud link
        nc_link = nextcloud.generate_public_link(file_meta.path)

        # Create table row
        row = TableRow.from_extract(
            extract=extract,
            subsidiary=path_info.subsidiary,
            contract_category=path_info.contract_category,
            city_from_path=path_info.city,
            year=path_info.year,
            nextcloud_link=nc_link,
            filename=file_meta.filename,
            source_folder=path_info.folder,
            file_id_hash=file_meta.file_id
        )

        # Save to Google Sheets
        gsheets.append_row(row)

        logger.info(f"Successfully processed: {file_meta.filename}")
        return True

    except Exception as e:
        logger.error(f"Error processing {file_meta.filename}: {e}")
        return False


def process_deleted_file(
    file_id: str,
    gsheets: GoogleSheetsClient
) -> bool:
    """
    Mark a deleted file in Google Sheets.

    Args:
        file_id: File ID hash
        gsheets: Google Sheets client

    Returns:
        True if successful, False otherwise
    """
    logger = get_logger("watcher")

    try:
        result = gsheets.mark_as_deleted(file_id)
        if result:
            logger.info(f"Marked as deleted: {file_id}")
        return result
    except Exception as e:
        logger.error(f"Error marking {file_id} as deleted: {e}")
        return False


def run_watcher_cycle(
    nextcloud: NextcloudClient,
    llm: LLMEngine,
    gsheets: GoogleSheetsClient,
    target_folders: List[str],
    state_path: str
) -> None:
    """
    Run a single watcher cycle.

    Args:
        nextcloud: Nextcloud client
        llm: LLM engine
        gsheets: Google Sheets client
        target_folders: List of folders to monitor
        state_path: Path to state file
    """
    logger = get_logger("watcher")
    settings = get_settings()

    # Load previous state
    stored_state = load_state(state_path)
    stored_ids: Set[str] = set(stored_state.keys())

    # Scan current files
    logger.info("Scanning folders...")
    current_files = nextcloud.scan_multiple_folders(target_folders)
    current_ids: Set[str] = set(current_files.keys())

    # Detect changes
    new_ids = current_ids - stored_ids
    deleted_ids = stored_ids - current_ids

    logger.info(f"Found {len(new_ids)} new files, {len(deleted_ids)} deleted files")

    # --- Detect renames: match new and deleted files by size + parent folder ---
    renamed_pairs = []  # list of (old_file_id, new_file_id)

    if new_ids and deleted_ids:
        # Build lookup from deleted files: (size, parent_folder) -> (file_id, meta_dict)
        deleted_lookup: Dict[tuple, List[tuple]] = {}
        for del_id in deleted_ids:
            meta = stored_state.get(del_id, {})
            size = meta.get("size")
            path = meta.get("path", "")
            parent = get_folder_from_path(path) if path else ""
            if size is not None and parent:
                key = (size, parent)
                if key not in deleted_lookup:
                    deleted_lookup[key] = []
                deleted_lookup[key].append((del_id, meta))

        # Match new files against deleted files
        for new_id in list(new_ids):
            new_meta = current_files[new_id]
            parent = get_folder_from_path(new_meta.path)
            key = (new_meta.size, parent)

            if key in deleted_lookup and deleted_lookup[key]:
                old_id, old_meta = deleted_lookup[key].pop(0)
                if not deleted_lookup[key]:
                    del deleted_lookup[key]
                renamed_pairs.append((old_id, new_id))

    # Process renames
    for old_id, new_id in renamed_pairs:
        new_meta = current_files[new_id]
        nc_link = nextcloud.generate_public_link(new_meta.path)
        path_info = PathInfo(new_meta.path, target_folders)

        success = gsheets.update_row_metadata(
            old_file_id=old_id,
            new_filename=new_meta.filename,
            new_link=nc_link,
            new_source_folder=path_info.folder,
            new_file_id_hash=new_id
        )

        if success:
            logger.info(
                f"Renamed file detected: '{stored_state.get(old_id, {}).get('filename', '?')}' "
                f"-> '{new_meta.filename}'"
            )
        else:
            logger.warning(
                f"Rename detected but row update failed for {old_id} -> {new_id}, "
                f"will process as new file"
            )
            # Don't remove from new_ids/deleted_ids — let normal flow handle it
            continue

        # Update state: remove old, add new
        if old_id in stored_state:
            del stored_state[old_id]
        stored_state[new_id] = new_meta.to_dict()

        # Remove from new/deleted sets so they aren't processed again
        new_ids.discard(new_id)
        deleted_ids.discard(old_id)

    if renamed_pairs:
        save_state(state_path, stored_state)
        logger.info(f"Processed {len(renamed_pairs)} renamed files")

    # Process new files
    for file_id in new_ids:
        file_meta = current_files[file_id]

        # Check for duplicates (file might have been moved, e.g., when city folders are added)
        if settings.skip_duplicates:
            path_info = PathInfo(file_meta.path, target_folders)
            year = extract_year(file_meta.path)

            duplicate = gsheets.find_duplicate_by_filename(
                filename=file_meta.filename,
                subsidiary=path_info.subsidiary,
                year=year
            )

            if duplicate:
                worksheet, row_num, existing_file_id = duplicate
                logger.info(
                    f"Skipping duplicate (file moved?): {file_meta.filename} "
                    f"already exists in {worksheet.title} row {row_num}"
                )
                # Still update state to track this file
                stored_state[file_id] = file_meta.to_dict()
                save_state(state_path, stored_state)
                continue

        success = process_new_file(
            file_meta,
            nextcloud,
            llm,
            gsheets,
            target_folders
        )

        if success:
            # Update state
            stored_state[file_id] = file_meta.to_dict()
            save_state(state_path, stored_state)

        # Respect rate limits
        time.sleep(settings.api_request_delay)

    # Process deleted files
    for file_id in deleted_ids:
        process_deleted_file(file_id, gsheets)
        # Remove from state
        del stored_state[file_id]

    # Save final state
    save_state(state_path, stored_state)
    logger.info("Watcher cycle complete")


def main():
    """Main entry point for watcher script."""
    # Initialize logging
    logger = setup_logging()
    logger.info("Starting Contract Watcher")

    # Load settings
    settings = get_settings()

    # Initialize clients
    logger.info("Initializing clients...")

    nextcloud = NextcloudClient(
        host=settings.nc_host,
        username=settings.nc_user,
        password=settings.nc_password
    )

    llm = LLMEngine(
        api_key=settings.gemini_api_key,
        model_name=settings.gemini_model,
        request_delay=settings.api_request_delay
    )

    gsheets = GoogleSheetsClient(
        credentials_path=str(settings.google_credentials_full_path),
        spreadsheet_id=settings.spreadsheet_id
    )

    target_folders = settings.target_folders_list

    logger.info(f"Monitoring {len(target_folders)} folders")
    logger.info(f"Poll interval: {settings.poll_interval} seconds")

    # Main loop
    while True:
        try:
            run_watcher_cycle(
                nextcloud=nextcloud,
                llm=llm,
                gsheets=gsheets,
                target_folders=target_folders,
                state_path=settings.state_file_path
            )
        except KeyboardInterrupt:
            logger.info("Watcher stopped by user")
            break
        except Exception as e:
            logger.error(f"Error in watcher cycle: {e}")

        # Wait for next cycle
        logger.info(f"Waiting {settings.poll_interval} seconds until next cycle...")
        time.sleep(settings.poll_interval)


if __name__ == "__main__":
    main()
