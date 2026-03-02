"""
Google Sheets client for storing contract data.
Supports multiple sheets organized by year.
Metadata (file_id_hash, processed_at, source_folder) is stored in a hidden _meta sheet.
"""

from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from ..models import TableRow, ContractStatus
from ..utils import get_logger

logger = get_logger("gsheets")

# Google Sheets API scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Internal metadata sheet name (not visible to users as data sheet)
META_SHEET_NAME = "_meta"
META_HEADERS = ["file_id_hash", "sheet_name", "row_number", "processed_at", "source_folder"]

# Column indices in main sheet (0-based, A-M, 13 columns)
COL_SUBSIDIARY = 3    # D
COL_STATUS = 9        # J
COL_LINK = 11         # L
COL_FILENAME = 12     # M

# Column indices in _meta sheet (0-based)
META_COL_FILE_ID = 0
META_COL_SHEET = 1
META_COL_ROW = 2
META_COL_PROCESSED_AT = 3
META_COL_SOURCE_FOLDER = 4


class GoogleSheetsClient:
    """Client for Google Sheets operations."""

    def __init__(
        self,
        credentials_path: str,
        spreadsheet_id: str
    ):
        self.spreadsheet_id = spreadsheet_id

        creds_path = Path(credentials_path)
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {credentials_path}"
            )

        credentials = Credentials.from_service_account_file(
            str(creds_path),
            scopes=SCOPES
        )

        self.gc = gspread.authorize(credentials)
        self.spreadsheet = self.gc.open_by_key(spreadsheet_id)

        logger.info(f"Google Sheets client initialized for spreadsheet: {spreadsheet_id}")

    # ------------------------------------------------------------------
    # Sheet management
    # ------------------------------------------------------------------

    def get_or_create_sheet(self, year: Optional[int]) -> gspread.Worksheet:
        """Get or create a data worksheet for the given year."""
        sheet_name = str(year) if year else "Без года"

        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            logger.debug(f"Found existing sheet: {sheet_name}")
            return worksheet

        except gspread.WorksheetNotFound:
            logger.info(f"Creating new sheet: {sheet_name}")
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name,
                rows=1000,
                cols=15
            )

            headers = TableRow.get_headers()
            worksheet.update("A1", [headers])

            # Format header row (bold, grey background) — A1:M1 (13 columns)
            worksheet.format("A1:M1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
            })

            return worksheet

    def _get_or_create_meta_sheet(self) -> gspread.Worksheet:
        """Get or create the internal _meta worksheet."""
        try:
            return self.spreadsheet.worksheet(META_SHEET_NAME)
        except gspread.WorksheetNotFound:
            logger.info(f"Creating metadata sheet: {META_SHEET_NAME}")
            worksheet = self.spreadsheet.add_worksheet(
                title=META_SHEET_NAME,
                rows=5000,
                cols=6
            )
            worksheet.update("A1", [META_HEADERS])
            worksheet.format("A1:E1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.95}
            })
            return worksheet

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def append_row(self, row: TableRow) -> None:
        """Append a row to the appropriate sheet and record metadata."""
        worksheet = self.get_or_create_sheet(row.year)

        # Determine row number before appending
        current_row_count = len(worksheet.get_all_values())
        new_row_num = current_row_count + 1

        values = row.to_sheets_row()
        worksheet.append_row(values, value_input_option="USER_ENTERED")

        # Write metadata to _meta sheet
        sheet_name = str(row.year) if row.year else "Без года"
        self._write_meta_row(
            file_id_hash=row.file_id_hash,
            sheet_name=sheet_name,
            row_number=new_row_num,
            processed_at=row.processed_at,
            source_folder=row.source_folder
        )

        logger.info(f"Appended row for {row.filename} to sheet {row.year} (row {new_row_num})")

    def append_rows_batch(
        self,
        rows: List[TableRow],
        batch_size: int = 10
    ) -> int:
        """Append multiple rows in batches, recording metadata for each."""
        # Group by year
        by_year: Dict[Optional[int], List[TableRow]] = {}
        for row in rows:
            year = row.year
            if year not in by_year:
                by_year[year] = []
            by_year[year].append(row)

        total_written = 0

        for year, year_rows in by_year.items():
            worksheet = self.get_or_create_sheet(year)
            sheet_name = str(year) if year else "Без года"

            values = [row.to_sheets_row() for row in year_rows]

            # Write in batches
            for i in range(0, len(values), batch_size):
                batch_values = values[i:i + batch_size]
                batch_rows = year_rows[i:i + batch_size]

                # Row number of first row in this batch
                current_count = len(worksheet.get_all_values())
                first_row_num = current_count + 1

                worksheet.append_rows(batch_values, value_input_option="USER_ENTERED")

                # Write metadata for each row in the batch
                meta_rows = []
                for j, row in enumerate(batch_rows):
                    meta_rows.append([
                        row.file_id_hash,
                        sheet_name,
                        str(first_row_num + j),
                        row.processed_at,
                        row.source_folder
                    ])

                meta_sheet = self._get_or_create_meta_sheet()
                meta_sheet.append_rows(meta_rows, value_input_option="RAW")

                total_written += len(batch_values)
                logger.debug(f"Wrote batch of {len(batch_values)} rows to sheet {year}")

        logger.info(f"Total rows written: {total_written}")
        return total_written

    def _write_meta_row(
        self,
        file_id_hash: str,
        sheet_name: str,
        row_number: int,
        processed_at: str,
        source_folder: str
    ) -> None:
        """Append one record to the _meta sheet."""
        meta_sheet = self._get_or_create_meta_sheet()
        meta_sheet.append_row(
            [file_id_hash, sheet_name, str(row_number), processed_at, source_folder],
            value_input_option="RAW"
        )

    # ------------------------------------------------------------------
    # Lookup operations
    # ------------------------------------------------------------------

    def find_row_by_file_id(
        self,
        file_id: str
    ) -> Optional[Tuple[gspread.Worksheet, int]]:
        """
        Find a data row by file_id_hash using the _meta sheet.

        Returns:
            Tuple of (worksheet, row_number) if found, None otherwise.
        """
        meta_sheet = self._get_or_create_meta_sheet()

        try:
            cell = meta_sheet.find(file_id, in_column=META_COL_FILE_ID + 1)
        except gspread.CellNotFound:
            logger.debug(f"File ID {file_id} not found in _meta")
            return None
        except Exception as e:
            logger.warning(f"Error searching _meta for {file_id}: {e}")
            return None

        meta_row = meta_sheet.row_values(cell.row)
        if len(meta_row) < 3:
            logger.warning(f"Incomplete _meta row for {file_id}")
            return None

        sheet_name = meta_row[META_COL_SHEET]
        try:
            row_number = int(meta_row[META_COL_ROW])
        except ValueError:
            logger.warning(f"Invalid row_number in _meta for {file_id}: {meta_row[META_COL_ROW]}")
            return None

        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            logger.warning(f"Sheet '{sheet_name}' not found for file_id {file_id}")
            return None

        logger.debug(f"Found file_id {file_id} in sheet '{sheet_name}' row {row_number}")
        return (worksheet, row_number)

    def _find_meta_row(self, file_id: str) -> Optional[Tuple[gspread.Worksheet, int]]:
        """
        Find the row in _meta sheet for the given file_id.

        Returns:
            Tuple of (_meta worksheet, meta_row_number) if found, None otherwise.
        """
        meta_sheet = self._get_or_create_meta_sheet()
        try:
            cell = meta_sheet.find(file_id, in_column=META_COL_FILE_ID + 1)
            return (meta_sheet, cell.row)
        except gspread.CellNotFound:
            return None
        except Exception as e:
            logger.warning(f"Error finding meta row for {file_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Update operations
    # ------------------------------------------------------------------

    def update_status_by_file_id(
        self,
        file_id: str,
        new_status: ContractStatus
    ) -> bool:
        """Update the status of a contract row (column J)."""
        result = self.find_row_by_file_id(file_id)

        if not result:
            logger.warning(f"Cannot update status: file_id {file_id} not found")
            return False

        worksheet, row_num = result
        worksheet.update(f"J{row_num}", new_status.value)

        logger.info(
            f"Updated status for {file_id} to '{new_status.value}' "
            f"in sheet {worksheet.title}"
        )
        return True

    def update_row_metadata(
        self,
        old_file_id: str,
        new_filename: str,
        new_link: str,
        new_source_folder: str,
        new_file_id_hash: str
    ) -> bool:
        """
        Update file metadata for a renamed file.

        Updates columns L (hyperlink) and M (filename) in the main sheet,
        and updates file_id_hash + source_folder in _meta.
        """
        result = self.find_row_by_file_id(old_file_id)
        if not result:
            logger.warning(f"Cannot update metadata: file_id {old_file_id} not found")
            return False

        worksheet, row_num = result

        # Update L:M in main sheet (hyperlink + filename)
        hyperlink = f'=HYPERLINK("{new_link}";"Открыть")'
        worksheet.update(
            f"L{row_num}:M{row_num}",
            [[hyperlink, new_filename]],
            value_input_option="USER_ENTERED"
        )

        # Update _meta row: file_id_hash (col A) and source_folder (col E)
        meta_result = self._find_meta_row(old_file_id)
        if meta_result:
            meta_sheet, meta_row_num = meta_result
            meta_row = meta_sheet.row_values(meta_row_num)
            # Ensure row has enough columns
            while len(meta_row) < 5:
                meta_row.append("")
            meta_row[META_COL_FILE_ID] = new_file_id_hash
            meta_row[META_COL_SOURCE_FOLDER] = new_source_folder
            meta_sheet.update(
                f"A{meta_row_num}:E{meta_row_num}",
                [meta_row[:5]],
                value_input_option="RAW"
            )

        logger.info(
            f"Updated metadata for renamed file: {old_file_id} -> "
            f"{new_file_id_hash} ({new_filename}) in sheet {worksheet.title}"
        )
        return True

    def mark_as_deleted(self, file_id: str) -> bool:
        """Mark a contract as deleted from source."""
        return self.update_status_by_file_id(
            file_id,
            ContractStatus.DELETED_FROM_SOURCE
        )

    # ------------------------------------------------------------------
    # Query / duplicate detection
    # ------------------------------------------------------------------

    def get_all_file_ids(self) -> Set[str]:
        """Get all file IDs from the _meta sheet."""
        file_ids: Set[str] = set()

        try:
            meta_sheet = self._get_or_create_meta_sheet()
            # Column A = file_id_hash (gspread col_values uses 1-based index)
            col_values = meta_sheet.col_values(META_COL_FILE_ID + 1)
            for value in col_values[1:]:  # skip header
                if value:
                    file_ids.add(value)
        except Exception as e:
            logger.warning(f"Error reading _meta sheet: {e}")

        logger.debug(f"Found {len(file_ids)} file IDs in _meta")
        return file_ids

    def row_exists(self, file_id: str) -> bool:
        """Check if a row with given file_id exists."""
        return self.find_row_by_file_id(file_id) is not None

    def find_duplicate_by_filename(
        self,
        filename: str,
        subsidiary: str,
        year: Optional[int]
    ) -> Optional[Tuple[gspread.Worksheet, int, str]]:
        """
        Find a potential duplicate entry by filename + subsidiary in the year sheet.

        Returns:
            Tuple of (worksheet, row_number, file_id_hash) if found, None otherwise.
        """
        sheet_name = str(year) if year else "Без года"

        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return None

        try:
            all_values = worksheet.get_all_values()
            if len(all_values) <= 1:
                return None

            # Build a meta index for this sheet: row_number_str -> file_id_hash
            meta_index = self._build_meta_index_for_sheet(sheet_name)

            for row_idx, row in enumerate(all_values[1:], start=2):
                row_filename = row[COL_FILENAME] if len(row) > COL_FILENAME else ""
                row_subsidiary = row[COL_SUBSIDIARY] if len(row) > COL_SUBSIDIARY else ""

                if row_filename == filename and row_subsidiary == subsidiary:
                    file_id = meta_index.get(str(row_idx), "")
                    logger.info(
                        f"Found potential duplicate: {filename} "
                        f"(subsidiary={subsidiary}, year={year}) at row {row_idx}"
                    )
                    return (worksheet, row_idx, file_id)

        except Exception as e:
            logger.warning(f"Error checking for duplicates: {e}")

        return None

    def get_existing_filenames_for_year(
        self,
        year: Optional[int]
    ) -> Dict[Tuple[str, str], str]:
        """
        Get all existing (filename, subsidiary) -> file_id_hash for a specific year.

        Used for efficient duplicate detection during batch processing.
        """
        result: Dict[Tuple[str, str], str] = {}
        sheet_name = str(year) if year else "Без года"

        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return result

        try:
            all_values = worksheet.get_all_values()
            if len(all_values) <= 1:
                return result

            # Build meta index: row_number_str -> file_id_hash
            meta_index = self._build_meta_index_for_sheet(sheet_name)

            for row_idx, row in enumerate(all_values[1:], start=2):
                row_filename = row[COL_FILENAME] if len(row) > COL_FILENAME else ""
                row_subsidiary = row[COL_SUBSIDIARY] if len(row) > COL_SUBSIDIARY else ""
                file_id = meta_index.get(str(row_idx), "")

                if row_filename and row_subsidiary:
                    result[(row_filename, row_subsidiary)] = file_id

        except Exception as e:
            logger.warning(f"Error getting existing filenames for year {year}: {e}")

        return result

    def _build_meta_index_for_sheet(
        self,
        sheet_name: str
    ) -> Dict[str, str]:
        """
        Build a row_number -> file_id_hash index for a specific sheet from _meta.

        Returns:
            Dict mapping row_number_str to file_id_hash.
        """
        index: Dict[str, str] = {}
        try:
            meta_sheet = self._get_or_create_meta_sheet()
            all_meta = meta_sheet.get_all_values()
            for meta_row in all_meta[1:]:  # skip header
                if len(meta_row) >= 3 and meta_row[META_COL_SHEET] == sheet_name:
                    row_num_str = meta_row[META_COL_ROW]
                    file_id = meta_row[META_COL_FILE_ID]
                    if row_num_str and file_id:
                        index[row_num_str] = file_id
        except Exception as e:
            logger.warning(f"Error building meta index for sheet '{sheet_name}': {e}")
        return index
