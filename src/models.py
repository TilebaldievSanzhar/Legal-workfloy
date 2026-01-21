"""
Pydantic models and enums for the ETL pipeline.
"""

from enum import Enum
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel, Field


class ContractStatus(str, Enum):
    """Contract status enumeration."""

    ACTIVE = "действует"
    EXECUTED = "исполнен"
    EXPIRED = "истек"
    EXTENDED = "продлен"
    TERMINATED = "расторжение"
    CLAIM = "претензия"
    LITIGATION = "суд. разбирательство"
    DELETED_FROM_SOURCE = "удален из реестра"
    UNKNOWN = "требует проверки"


class ContractExtract(BaseModel):
    """
    Schema for data extracted from contract PDF via LLM.
    """

    contract_number: Optional[str] = Field(
        default=None,
        description="Номер договора"
    )
    counterparty_name: str = Field(
        description="Наименование контрагента"
    )
    start_date: Optional[date] = Field(
        default=None,
        description="Дата заключения договора"
    )
    end_date: Optional[date] = Field(
        default=None,
        description="Дата окончания договора. None если бессрочный"
    )
    is_perpetual: bool = Field(
        default=False,
        description="True если договор бессрочный"
    )
    status: ContractStatus = Field(
        default=ContractStatus.UNKNOWN,
        description="Статус договора на основе текста"
    )
    summary: str = Field(
        description="Краткая суть договора (до 10 слов)"
    )


class TableRow(BaseModel):
    """
    Schema for a row in Google Sheets.
    Extends ContractExtract with metadata fields.
    """

    # LLM-extracted fields
    contract_number: Optional[str] = Field(
        default=None,
        description="Номер договора"
    )
    counterparty_name: str = Field(
        description="Наименование контрагента"
    )
    start_date: Optional[date] = Field(
        default=None,
        description="Дата заключения договора"
    )
    end_date: Optional[date] = Field(
        default=None,
        description="Дата окончания договора"
    )
    is_perpetual: bool = Field(
        default=False,
        description="Бессрочный договор"
    )
    status: ContractStatus = Field(
        default=ContractStatus.UNKNOWN,
        description="Статус договора"
    )
    summary: str = Field(
        description="Суть договора"
    )

    # Path-derived fields
    subsidiary: str = Field(
        description="Дочерняя компания (из имени корневой папки)"
    )
    city: Optional[str] = Field(
        default=None,
        description="Город из пути файла"
    )
    year: Optional[int] = Field(
        default=None,
        description="Год из пути файла (для выбора листа)"
    )

    # File metadata
    nextcloud_link: str = Field(
        description="Ссылка на файл в Nextcloud"
    )
    filename: str = Field(
        description="Имя файла"
    )
    source_folder: str = Field(
        description="Полный путь к папке-источнику"
    )
    file_id_hash: str = Field(
        description="Уникальный ID файла для поиска строки"
    )
    processed_at: str = Field(
        description="Дата и время обработки"
    )

    @classmethod
    def from_extract(
        cls,
        extract: ContractExtract,
        subsidiary: str,
        city: Optional[str],
        year: Optional[int],
        nextcloud_link: str,
        filename: str,
        source_folder: str,
        file_id_hash: str
    ) -> "TableRow":
        """
        Create TableRow from ContractExtract and metadata.
        """
        return cls(
            contract_number=extract.contract_number,
            counterparty_name=extract.counterparty_name,
            start_date=extract.start_date,
            end_date=extract.end_date,
            is_perpetual=extract.is_perpetual,
            status=extract.status,
            summary=extract.summary,
            subsidiary=subsidiary,
            city=city,
            year=year,
            nextcloud_link=nextcloud_link,
            filename=filename,
            source_folder=source_folder,
            file_id_hash=file_id_hash,
            processed_at=datetime.now().isoformat()
        )

    def to_sheets_row(self) -> List[str]:
        """
        Convert to a list of strings for Google Sheets.
        Order matches the column structure defined in spec.
        """
        return [
            self.contract_number or "",
            self.counterparty_name,
            self.subsidiary,
            self.city or "",
            self.start_date.isoformat() if self.start_date else "",
            self.end_date.isoformat() if self.end_date else "",
            "Да" if self.is_perpetual else "Нет",
            self.status.value,
            self.summary,
            self.nextcloud_link,
            self.filename,
            self.source_folder,
            self.file_id_hash,
            self.processed_at
        ]

    @staticmethod
    def get_headers() -> List[str]:
        """Get column headers for Google Sheets."""
        return [
            "Номер договора",
            "Контрагент",
            "Дочерняя компания",
            "Город",
            "Дата заключения",
            "Дата окончания",
            "Бессрочный",
            "Статус",
            "Суть договора",
            "Ссылка Nextcloud",
            "Имя файла",
            "Папка-источник",
            "file_id_hash",
            "Дата обработки"
        ]


class FileMetadata(BaseModel):
    """Metadata for a file in Nextcloud."""

    file_id: str = Field(description="Unique file identifier")
    path: str = Field(description="Full path to file")
    filename: str = Field(description="File name")
    size: int = Field(description="File size in bytes")
    modified: datetime = Field(description="Last modification time")
    etag: Optional[str] = Field(default=None, description="ETag for change detection")

    def to_dict(self) -> dict:
        """Convert to dictionary for state storage."""
        return {
            "file_id": self.file_id,
            "path": self.path,
            "filename": self.filename,
            "size": self.size,
            "modified": self.modified.isoformat(),
            "etag": self.etag
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileMetadata":
        """Create from dictionary."""
        return cls(
            file_id=data["file_id"],
            path=data["path"],
            filename=data["filename"],
            size=data["size"],
            modified=datetime.fromisoformat(data["modified"]),
            etag=data.get("etag")
        )
