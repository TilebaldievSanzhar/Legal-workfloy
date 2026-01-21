"""
LLM Engine for contract data extraction using Google Gemini.
Includes rate limiting and retry logic for API calls.
"""

import json
import time
import base64
from typing import Optional

from google import genai
from google.genai import types
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from .models import ContractExtract, ContractStatus
from .utils import get_logger

logger = get_logger("llm_engine")


# Extraction prompt in Russian
EXTRACTION_PROMPT = """Проанализируй PDF-документ договора и извлеки следующую информацию.
Ответ ДОЛЖЕН быть строго в формате JSON без дополнительного текста.

Извлеки:
1. contract_number - номер договора (строка или null если не найден)
2. counterparty_name - наименование контрагента (вторая сторона договора)
3. start_date - дата заключения договора в формате YYYY-MM-DD (или null)
4. end_date - дата окончания договора в формате YYYY-MM-DD (или null если бессрочный)
5. is_perpetual - true если договор бессрочный, false если срочный
6. status - статус договора, одно из значений:
   - "действует" - договор активен
   - "исполнен" - договор исполнен
   - "истек" - срок действия истек
   - "продлен" - договор продлен
   - "расторжение" - договор расторгнут
   - "претензия" - имеется претензия
   - "суд. разбирательство" - судебное разбирательство
   - "требует проверки" - если статус неясен
7. summary - краткая суть договора (максимум 10 слов)

Пример ответа:
{
  "contract_number": "123/2024",
  "counterparty_name": "ООО Ромашка",
  "start_date": "2024-01-15",
  "end_date": "2025-01-14",
  "is_perpetual": false,
  "status": "действует",
  "summary": "Поставка офисной мебели"
}

ВАЖНО: Верни ТОЛЬКО JSON без markdown-форматирования и пояснений."""


class RateLimitError(Exception):
    """Raised when API rate limit is exceeded."""
    pass


class LLMEngine:
    """Engine for extracting contract data using Gemini LLM."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
        request_delay: float = 2.0
    ):
        """
        Initialize LLM Engine.

        Args:
            api_key: Google Gemini API key
            model_name: Model to use (default: gemini-2.0-flash)
            request_delay: Delay between requests in seconds
        """
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.request_delay = request_delay
        self._last_request_time: float = 0

        logger.info(f"LLM Engine initialized with model: {model_name}")

    def _wait_for_rate_limit(self) -> None:
        """Ensure minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            sleep_time = self.request_delay - elapsed
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f}s")
            time.sleep(sleep_time)

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        before_sleep=lambda retry_state: logger.warning(
            f"Rate limit hit, retrying in {retry_state.next_action.sleep}s..."
        )
    )
    def extract_contract_data(
        self,
        pdf_bytes: bytes,
        filename: str = "contract.pdf"
    ) -> ContractExtract:
        """
        Extract contract data from PDF using Gemini.

        Args:
            pdf_bytes: PDF file content as bytes
            filename: Original filename for logging

        Returns:
            ContractExtract with extracted data

        Raises:
            RateLimitError: If API rate limit is exceeded
            ValueError: If response cannot be parsed
        """
        self._wait_for_rate_limit()

        logger.info(f"Processing file: {filename}")

        try:
            # Encode PDF as base64
            pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

            # Create content with PDF
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(
                            data=pdf_bytes,
                            mime_type="application/pdf"
                        ),
                        types.Part.from_text(text=EXTRACTION_PROMPT)
                    ]
                )
            ]

            # Generate response
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=1024
                )
            )

            self._last_request_time = time.time()

            # Parse response
            return self._parse_response(response.text, filename)

        except Exception as e:
            error_str = str(e).lower()

            # Check for rate limit errors
            if "429" in error_str or "resource exhausted" in error_str:
                logger.warning(f"Rate limit exceeded for {filename}")
                raise RateLimitError(f"Rate limit exceeded: {e}")

            logger.error(f"Error processing {filename}: {e}")
            raise

    def _parse_response(
        self,
        response_text: str,
        filename: str
    ) -> ContractExtract:
        """
        Parse LLM response into ContractExtract.

        Args:
            response_text: Raw response from LLM
            filename: Original filename for error messages

        Returns:
            ContractExtract instance
        """
        try:
            # Clean response (remove markdown code blocks if present)
            text = response_text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            # Parse JSON
            data = json.loads(text)

            # Map status string to enum
            status_str = data.get("status", "требует проверки")
            status = self._map_status(status_str)

            return ContractExtract(
                contract_number=data.get("contract_number"),
                counterparty_name=data.get("counterparty_name", "Неизвестен"),
                start_date=data.get("start_date"),
                end_date=data.get("end_date"),
                is_perpetual=data.get("is_perpetual", False),
                status=status,
                summary=data.get("summary", "")
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON for {filename}: {e}")
            logger.debug(f"Raw response: {response_text}")

            # Return default with unknown status
            return ContractExtract(
                counterparty_name="Ошибка разбора",
                is_perpetual=False,
                status=ContractStatus.UNKNOWN,
                summary="Ошибка извлечения данных"
            )

    @staticmethod
    def _map_status(status_str: str) -> ContractStatus:
        """
        Map status string to ContractStatus enum.

        Args:
            status_str: Status string from LLM

        Returns:
            ContractStatus enum value
        """
        status_mapping = {
            "действует": ContractStatus.ACTIVE,
            "исполнен": ContractStatus.EXECUTED,
            "истек": ContractStatus.EXPIRED,
            "продлен": ContractStatus.EXTENDED,
            "расторжение": ContractStatus.TERMINATED,
            "расторгнут": ContractStatus.TERMINATED,
            "претензия": ContractStatus.CLAIM,
            "суд. разбирательство": ContractStatus.LITIGATION,
            "судебное разбирательство": ContractStatus.LITIGATION,
            "требует проверки": ContractStatus.UNKNOWN,
        }

        status_lower = status_str.lower().strip()
        return status_mapping.get(status_lower, ContractStatus.UNKNOWN)

    def extract_with_fallback(
        self,
        pdf_bytes: bytes,
        filename: str = "contract.pdf",
        fallback_model: str = "gemini-1.5-flash"
    ) -> ContractExtract:
        """
        Extract contract data with fallback to another model on failure.

        Args:
            pdf_bytes: PDF file content
            filename: Original filename
            fallback_model: Model to use if primary fails

        Returns:
            ContractExtract with extracted data
        """
        try:
            return self.extract_contract_data(pdf_bytes, filename)
        except Exception as e:
            logger.warning(
                f"Primary model failed for {filename}, trying fallback: {e}"
            )

            # Try with fallback model
            original_model = self.model_name
            self.model_name = fallback_model

            try:
                return self.extract_contract_data(pdf_bytes, filename)
            finally:
                self.model_name = original_model
