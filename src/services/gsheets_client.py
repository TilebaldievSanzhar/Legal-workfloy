"""
Google Sheets client for storing contract data.
Supports multiple sheets organized by year.
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


class GoogleSheetsClient:
    """Client for Google Sheets operations."""

    def __init__(
        self,
        credentials_path: str,
        spreadsheet_id: str
    ):
        """
        Initialize Google Sheets client.

        Args:
            credentials_path: Path to service account credentials JSON
            spreadsheet_id: ID of the target spreadsheet
        """
        self.spreadsheet_id = spreadsheet_id

        # Load credentials
        creds_path = Path(credentials_path)
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {credentials_path}"
            )

        credentials = Credentials.from_service_account_file(
            str(creds_path),
            scopes=SCOPES
        )

        # Initialize gspread client
        self.gc = gspread.authorize(credentials)
        self.spreadsheet = self.gc.open_by_key(spreadsheet_id)

        logger.info(f"Google Sheets client initialized for spreadsheet: {spreadsheet_id}")

    def get_or_create_sheet(self, year: Optional[int]) -> gspread.Worksheet:
        """
        Get or create a worksheet for the given year.

        Args:
            year: Year for the sheet (e.g., 2024). If None, uses "Без года"

        Returns:
            Worksheet instance
        """
        sheet_name = str(year) if year else "Без года"

        try:
            # Try to get existing sheet
            worksheet = self.spreadsheet.worksheet(sheet_name)
            logger.debug(f"Found existing sheet: {sheet_name}")
            return worksheet

        except gspread.WorksheetNotFound:
            # Create new sheet with headers
            logger.info(f"Creating new sheet: {sheet_name}")
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name,
                rows=1000,
                cols=20
            )

            # Add headers
            headers = TableRow.get_headers()
            worksheet.update("A1", [headers])

            # Format header row (bold)
            worksheet.format("A1:N1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
            })

            # Hide file_id_hash column (column M, index 13)
            # Note: gspread doesn't support hiding columns directly,
            # but we can document this for manual setup

            return worksheet

    def append_row(self, row: TableRow) -> None:
        """
        Append a row to the appropriate sheet based on year.

        Args:
            row: TableRow to append
        """
        worksheet = self.get_or_create_sheet(row.year)

        # Convert to list of values
        values = row.to_sheets_row()

        # Append row
        worksheet.append_row(values, value_input_option="USER_ENTERED")
        logger.info(f"Appended row for {row.filename} to sheet {row.year}")

    def append_rows_batch(
        self,
        rows: List[TableRow],
        batch_size: int = 10
    ) -> int:
        """
        Append multiple rows in batches for efficiency.

        Args:
            rows: List of TableRow objects
            batch_size: Number of rows to write per batch

        Returns:
            Number of rows written
        """
        # Group rows by year
        by_year: Dict[Optional[int], List[TableRow]] = {}
        for row in rows:
            year = row.year
            if year not in by_year:
                by_year[year] = []
            by_year[year].append(row)

        total_written = 0

        for year, year_rows in by_year.items():
            worksheet = self.get_or_create_sheet(year)

            # Convert to values
            values = [row.to_sheets_row() for row in year_rows]

            # Write in batches
            for i in range(0, len(values), batch_size):
                batch = values[i:i + batch_size]
                worksheet.append_rows(batch, value_input_option="USER_ENTERED")
                total_written += len(batch)
                logger.debug(f"Wrote batch of {len(batch)} rows to sheet {year}")

        logger.info(f"Total rows written: {total_written}")
        return total_written

    def find_row_by_file_id(
        self,
        file_id: str
    ) -> Optional[Tuple[gspread.Worksheet, int]]:
        """
        Find a row by file_id_hash across all sheets.

        Args:
            file_id: File ID hash to search for

        Returns:
            Tuple of (worksheet, row_number) if found, None otherwise
        """
        # Search in all worksheets
        for worksheet in self.spreadsheet.worksheets():
            try:
                # Get all values in file_id column (column M, index 13)
                cell = worksheet.find(file_id)
                if cell:
                    logger.debug(
                        f"Found file_id {file_id} in sheet {worksheet.title} "
                        f"at row {cell.row}"
                    )
                    return (worksheet, cell.row)
            except gspread.CellNotFound:
                continue
            except Exception as e:
                logger.warning(f"Error searching sheet {worksheet.title}: {e}")
                continue

        logger.debug(f"File ID {file_id} not found in any sheet")
        return None

    def update_status_by_file_id(
        self,
        file_id: str,
        new_status: ContractStatus
    ) -> bool:
        """
        Update the status of a contract by its file_id.

        Args:
            file_id: File ID hash
            new_status: New status to set

        Returns:
            True if updated, False if not found
        """
        result = self.find_row_by_file_id(file_id)

        if not result:
            logger.warning(f"Cannot update status: file_id {file_id} not found")
            return False

        worksheet, row_num = result

        # Status is in column H (index 8)
        status_col = "H"
        worksheet.update(f"{status_col}{row_num}", new_status.value)

        logger.info(
            f"Updated status for {file_id} to '{new_status.value}' "
            f"in sheet {worksheet.title}"
        )
        return True

    def mark_as_deleted(self, file_id: str) -> bool:
        """
        Mark a contract as deleted from source.

        Args:
            file_id: File ID hash

        Returns:
            True if updated, False if not found
        """
        return self.update_status_by_file_id(
            file_id,
            ContractStatus.DELETED_FROM_SOURCE
        )

    def get_all_file_ids(self) -> Set[str]:
        """
        Get all file IDs currently in the spreadsheet.

        Returns:
            Set of file ID hashes
        """
        file_ids: Set[str] = set()

        for worksheet in self.spreadsheet.worksheets():
            try:
                # Get file_id column (column M)
                col_values = worksheet.col_values(13)  # Column M is index 13

                # Skip header
                for value in col_values[1:]:
                    if value:
                        file_ids.add(value)

            except Exception as e:
                logger.warning(f"Error reading sheet {worksheet.title}: {e}")

        logger.debug(f"Found {len(file_ids)} file IDs in spreadsheet")
        return file_ids

    def row_exists(self, file_id: str) -> bool:
        """
        Check if a row with given file_id exists.

        Args:
            file_id: File ID hash

        Returns:
            True if exists, False otherwise
        """
        return self.find_row_by_file_id(file_id) is not None

    def find_duplicate_by_filename(
        self,
        filename: str,
        subsidiary: str,
        year: Optional[int]
    ) -> Optional[Tuple[gspread.Worksheet, int, str]]:
        """
        Find a potential duplicate entry by filename, subsidiary, and year.

        This helps detect files that were moved (e.g., when city folders are added)
        but represent the same contract.

        Args:
            filename: File name to search for
            subsidiary: Subsidiary/company name
            year: Year from path

        Returns:
            Tuple of (worksheet, row_number, existing_file_id) if found, None otherwise
        """
        # Try to find in the year-specific sheet first
        sheet_name = str(year) if year else "Без года"

        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return None

        try:
            # Get all data from the sheet
            all_values = worksheet.get_all_values()
            if len(all_values) <= 1:  # Only header or empty
                return None

            # Column indices (0-based):
            # 2 = Контрагент (counterparty)
            # 3 = Дочерняя компания (subsidiary)
            # 10 = Имя файла (filename)
            # 12 = file_id_hash
            filename_col = 10
            subsidiary_col = 3
            file_id_col = 12

            for row_idx, row in enumerate(all_values[1:], start=2):  # Skip header
                if len(row) > max(filename_col, subsidiary_col, file_id_col):
                    row_filename = row[filename_col] if len(row) > filename_col else ""
                    row_subsidiary = row[subsidiary_col] if len(row) > subsidiary_col else ""
                    row_file_id = row[file_id_col] if len(row) > file_id_col else ""

                    # Check for match
                    if row_filename == filename and row_subsidiary == subsidiary:
                        logger.info(
                            f"Found potential duplicate: {filename} "
                            f"(subsidiary={subsidiary}, year={year}) "
                            f"at row {row_idx}"
                        )
                        return (worksheet, row_idx, row_file_id)

        except Exception as e:
            logger.warning(f"Error checking for duplicates: {e}")

        return None

    def get_existing_filenames_for_year(
        self,
        year: Optional[int]
    ) -> Dict[Tuple[str, str], str]:
        """
        Get all existing filename+subsidiary combinations for a specific year.

        This is more efficient for batch processing than checking one by one.

        Args:
            year: Year to check

        Returns:
            Dictionary mapping (filename, subsidiary) to file_id_hash
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

            # Column indices
            filename_col = 10
            subsidiary_col = 3
            file_id_col = 12

            for row in all_values[1:]:
                if len(row) > max(filename_col, subsidiary_col, file_id_col):
                    filename = row[filename_col] if len(row) > filename_col else ""
                    subsidiary = row[subsidiary_col] if len(row) > subsidiary_col else ""
                    file_id = row[file_id_col] if len(row) > file_id_col else ""

                    if filename and subsidiary:
                        result[(filename, subsidiary)] = file_id

        except Exception as e:
            logger.warning(f"Error getting existing filenames for year {year}: {e}")

        return result
