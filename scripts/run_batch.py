#!/usr/bin/env python3
"""
Batch processing script for historical data.
Processes all existing files with throttling to respect API limits.
"""

import json
import sys
import time
from pathlib import Path
from typing import Dict, List

from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings
from src.models import FileMetadata, TableRow
from src.llm_engine import LLMEngine, RateLimitError
from src.path_parser import PathInfo
from src.services.nextcloud_client import NextcloudClient
from src.services.gsheets_client import GoogleSheetsClient
from src.utils import setup_logging, get_logger


def load_state(state_path: str) -> Dict[str, dict]:
    """Load processed files state from JSON file."""
    path = Path(state_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state_path: str, state: Dict[str, dict]) -> None:
    """Save processed files state to JSON file."""
    path = Path(state_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def process_file(
    file_meta: FileMetadata,
    nextcloud: NextcloudClient,
    llm: LLMEngine,
    target_folders: list[str]
) -> TableRow | None:
    """
    Process a single file and return TableRow.

    Args:
        file_meta: File metadata
        nextcloud: Nextcloud client
        llm: LLM engine
        target_folders: List of target folders

    Returns:
        TableRow if successful, None otherwise
    """
    logger = get_logger("batch")

    try:
        # Download file
        pdf_bytes = nextcloud.download_file(file_meta.path)

        # Extract data with LLM
        extract = llm.extract_with_fallback(pdf_bytes, file_meta.filename)

        # Parse path for metadata
        path_info = PathInfo(file_meta.path, target_folders)

        # Generate Nextcloud link
        nc_link = nextcloud.generate_public_link(file_meta.path)

        # Create table row
        row = TableRow.from_extract(
            extract=extract,
            subsidiary=path_info.subsidiary,
            city=path_info.city,
            year=path_info.year,
            nextcloud_link=nc_link,
            filename=file_meta.filename,
            source_folder=path_info.folder,
            file_id_hash=file_meta.file_id
        )

        return row

    except RateLimitError:
        logger.warning(f"Rate limit hit for {file_meta.filename}, will retry")
        raise
    except Exception as e:
        logger.error(f"Error processing {file_meta.filename}: {e}")
        return None


def run_batch(
    nextcloud: NextcloudClient,
    llm: LLMEngine,
    gsheets: GoogleSheetsClient,
    target_folders: list[str],
    state_path: str,
    batch_size: int,
    request_delay: float
) -> None:
    """
    Run batch processing of all files.

    Args:
        nextcloud: Nextcloud client
        llm: LLM engine
        gsheets: Google Sheets client
        target_folders: List of folders to process
        state_path: Path to state file
        batch_size: Number of files per batch
        request_delay: Delay between requests
    """
    logger = get_logger("batch")

    # Load previous state
    stored_state = load_state(state_path)
    processed_ids = set(stored_state.keys())

    # Also check what's already in Google Sheets
    logger.info("Checking existing records in Google Sheets...")
    sheets_ids = gsheets.get_all_file_ids()
    processed_ids.update(sheets_ids)

    logger.info(f"Already processed: {len(processed_ids)} files")

    # Scan all target folders
    logger.info("Scanning all folders...")
    all_files = nextcloud.scan_multiple_folders(target_folders)

    # Filter out already processed files
    files_to_process: List[FileMetadata] = [
        meta for file_id, meta in all_files.items()
        if file_id not in processed_ids
    ]

    logger.info(f"Files to process: {len(files_to_process)}")

    if not files_to_process:
        logger.info("No new files to process")
        return

    # Process files with progress bar
    processed_count = 0
    error_count = 0
    batch_rows: List[TableRow] = []

    with tqdm(total=len(files_to_process), desc="Processing") as pbar:
        for i, file_meta in enumerate(files_to_process):
            try:
                # Process file
                row = process_file(file_meta, nextcloud, llm, target_folders)

                if row:
                    batch_rows.append(row)
                    processed_count += 1

                    # Update state
                    stored_state[file_meta.file_id] = file_meta.to_dict()

                    # Write batch to sheets
                    if len(batch_rows) >= batch_size:
                        logger.info(f"Writing batch of {len(batch_rows)} rows...")
                        gsheets.append_rows_batch(batch_rows, batch_size)
                        save_state(state_path, stored_state)
                        batch_rows = []

                        # Extra pause after batch
                        time.sleep(request_delay * 2)
                else:
                    error_count += 1

            except RateLimitError:
                # Extended pause on rate limit
                logger.warning("Rate limit exceeded, pausing for 60 seconds...")
                time.sleep(60)

                # Retry the file
                try:
                    row = process_file(file_meta, nextcloud, llm, target_folders)
                    if row:
                        batch_rows.append(row)
                        processed_count += 1
                        stored_state[file_meta.file_id] = file_meta.to_dict()
                except Exception as e:
                    logger.error(f"Retry failed for {file_meta.filename}: {e}")
                    error_count += 1

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                error_count += 1

            # Update progress bar
            pbar.update(1)
            pbar.set_postfix({"ok": processed_count, "err": error_count})

            # Regular delay between files
            time.sleep(request_delay)

    # Write remaining rows
    if batch_rows:
        logger.info(f"Writing final batch of {len(batch_rows)} rows...")
        gsheets.append_rows_batch(batch_rows, batch_size)
        save_state(state_path, stored_state)

    # Summary
    logger.info("=" * 50)
    logger.info("Batch processing complete")
    logger.info(f"Processed: {processed_count}")
    logger.info(f"Errors: {error_count}")
    logger.info(f"Total in state: {len(stored_state)}")


def main():
    """Main entry point for batch script."""
    # Initialize logging
    logger = setup_logging()
    logger.info("Starting Batch Processor")

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

    logger.info(f"Processing {len(target_folders)} folders")
    logger.info(f"Batch size: {settings.batch_size}")
    logger.info(f"Request delay: {settings.api_request_delay}s")

    # Run batch processing
    try:
        run_batch(
            nextcloud=nextcloud,
            llm=llm,
            gsheets=gsheets,
            target_folders=target_folders,
            state_path=settings.state_file_path,
            batch_size=settings.batch_size,
            request_delay=settings.api_request_delay
        )
    except KeyboardInterrupt:
        logger.info("Batch processing interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
