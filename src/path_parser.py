"""
Path parsing utilities for extracting metadata from file paths.
Extracts subsidiary name, city, and year from Nextcloud file paths.
"""

import re
from typing import Optional, List
from pathlib import PurePosixPath


def extract_subsidiary(path: str, target_folders: List[str]) -> str:
    """
    Extract subsidiary (company) name from the root folder of the path.

    The subsidiary is determined by matching the path against target folders.
    Returns the folder name from TARGET_FOLDERS that the path belongs to.

    Args:
        path: Full file path (e.g., "/Company1/Contracts/2024/Moscow/contract.pdf")
        target_folders: List of monitored folders (e.g., ["/Company1/Contracts", ...])

    Returns:
        Subsidiary name (folder name before /Contracts or the root folder name)
    """
    path_normalized = path.replace("\\", "/")

    for folder in target_folders:
        folder_normalized = folder.replace("\\", "/").rstrip("/")
        if path_normalized.startswith(folder_normalized):
            # Extract the company name from the target folder
            # e.g., "/Company1/Contracts" -> "Company1"
            parts = folder_normalized.strip("/").split("/")
            if parts:
                return parts[-1]

    # Fallback: extract first folder from path
    parts = path_normalized.strip("/").split("/")
    return parts[-1] if parts else "Unknown"


def extract_city(path: str) -> Optional[str]:
    """
    Extract city name from the file path.

    Looks for Kyrgyz and Russian city names in the path.
    Cities are typically folder names in the path structure.
    Supports formats: "г. Бишкек", "г.Бишкек", "Бишкек"

    Args:
        path: Full file path

    Returns:
        City name if found, None otherwise
    """
    # Kyrgyz cities (primary focus)
    kyrgyz_cities = [
        "Бишкек", "Bishkek",
        "Ош", "Osh",
        "Токмок", "Tokmok",
        "Кант", "Kant",
        "Каракол", "Karakol",
        "Джалал-Абад", "Jalal-Abad", "Джалалабад",
        "Нарын", "Naryn",
        "Талас", "Talas",
        "Баткен", "Batken",
        "Кызыл-Кия", "Kyzyl-Kiya",
        "Узген", "Uzgen", "Өзгөн",
        "Кара-Балта", "Kara-Balta",
        "Майлуу-Суу", "Mailuu-Suu",
        "Таш-Кумыр", "Tash-Kumyr",
        "Кок-Жангак", "Kok-Jangak",
        "Исфана", "Isfana",
        "Сулюкта", "Sulukta",
        "Кара-Суу", "Kara-Suu",
        "Балыкчы", "Balykchy",
        "Чолпон-Ата", "Cholpon-Ata",
    ]

    # Russian cities (for compatibility)
    russian_cities = [
        "Москва", "Moscow",
        "Санкт-Петербург", "СПб", "Saint-Petersburg", "St. Petersburg",
        "Новосибирск", "Novosibirsk",
        "Екатеринбург", "Yekaterinburg",
        "Казань", "Kazan",
        "Нижний Новгород", "Nizhny Novgorod",
        "Челябинск", "Chelyabinsk",
        "Самара", "Samara",
        "Омск", "Omsk",
        "Ростов-на-Дону", "Rostov-on-Don",
        "Уфа", "Ufa",
        "Красноярск", "Krasnoyarsk",
        "Воронеж", "Voronezh",
        "Пермь", "Perm",
        "Волгоград", "Volgograd",
        "Краснодар", "Krasnodar",
        "Саратов", "Saratov",
        "Тюмень", "Tyumen",
        "Тольятти", "Tolyatti",
        "Ижевск", "Izhevsk",
        "Барнаул", "Barnaul",
        "Ульяновск", "Ulyanovsk",
        "Иркутск", "Irkutsk",
        "Хабаровск", "Khabarovsk",
        "Ярославль", "Yaroslavl",
        "Владивосток", "Vladivostok",
        "Махачкала", "Makhachkala",
        "Томск", "Tomsk",
        "Оренбург", "Orenburg",
        "Кемерово", "Kemerovo",
        "Новокузнецк", "Novokuznetsk",
        "Рязань", "Ryazan",
        "Астрахань", "Astrakhan",
        "Набережные Челны", "Naberezhnye Chelny",
        "Пенза", "Penza",
        "Липецк", "Lipetsk",
        "Киров", "Kirov",
        "Чебоксары", "Cheboksary",
        "Тула", "Tula",
        "Калининград", "Kaliningrad",
        "Сочи", "Sochi",
    ]

    # Combine: Kyrgyz cities first (priority)
    known_cities = kyrgyz_cities + russian_cities

    path_normalized = path.replace("\\", "/")
    path_parts = path_normalized.split("/")

    # Helper to clean city prefix like "г. " or "г."
    def clean_city_prefix(s: str) -> str:
        s = s.strip()
        if s.lower().startswith("г. "):
            return s[3:].strip()
        if s.lower().startswith("г."):
            return s[2:].strip()
        return s

    # Check each path component against known cities
    for part in path_parts:
        part_clean = clean_city_prefix(part)
        part_lower = part_clean.lower()
        for city in known_cities:
            if city.lower() == part_lower:
                return city

    # If no exact match, try partial match for city folders
    for part in path_parts:
        part_clean = clean_city_prefix(part)
        for city in known_cities:
            if city.lower() in part_clean.lower():
                return city

    return None


def extract_year(path: str) -> Optional[int]:
    """
    Extract year from the file path.

    Looks for 4-digit year patterns (2000-2099) in folder names.

    Args:
        path: Full file path

    Returns:
        Year as integer if found, None otherwise
    """
    # Pattern for years 2000-2099
    year_pattern = re.compile(r"\b(20\d{2})\b")

    path_normalized = path.replace("\\", "/")

    # Search for year in path
    matches = year_pattern.findall(path_normalized)

    if matches:
        # Return the first (most recent in path) year found
        # Usually folders are structured like /Company/Contracts/2024/...
        for match in matches:
            year = int(match)
            # Validate reasonable range
            if 2000 <= year <= 2099:
                return year

    return None


def get_folder_from_path(path: str) -> str:
    """
    Extract the parent folder path from a file path.

    Args:
        path: Full file path

    Returns:
        Parent folder path
    """
    path_obj = PurePosixPath(path.replace("\\", "/"))
    return str(path_obj.parent)


def get_filename_from_path(path: str) -> str:
    """
    Extract the filename from a file path.

    Args:
        path: Full file path

    Returns:
        Filename
    """
    path_obj = PurePosixPath(path.replace("\\", "/"))
    return path_obj.name


class PathInfo:
    """Container for parsed path information."""

    def __init__(
        self,
        path: str,
        target_folders: List[str]
    ):
        """
        Parse path and extract all metadata.

        Args:
            path: Full file path
            target_folders: List of monitored folders
        """
        self.path = path
        self.subsidiary = extract_subsidiary(path, target_folders)
        self.city = extract_city(path)
        self.year = extract_year(path)
        self.folder = get_folder_from_path(path)
        self.filename = get_filename_from_path(path)

    def __repr__(self) -> str:
        return (
            f"PathInfo(subsidiary={self.subsidiary!r}, "
            f"city={self.city!r}, year={self.year}, "
            f"filename={self.filename!r})"
        )
