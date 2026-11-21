"""
Sarvam Document AI OCR Service
Language-independent OCR supporting all 22 Indian languages and English.
Uses Sarvam's Indic default engine with controlled single-pass fallback for English.
"""

import io
import json
import logging
import os
import re
import time
import zipfile
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sarvamai import SarvamAI

from .script_detection import detect_scripts, map_scripts_to_languages

load_dotenv()
logger = logging.getLogger(__name__)

# Request and polling configuration
MAX_WAIT_SECONDS = 180
INITIAL_POLL_INTERVAL = 2
MAX_POLL_INTERVAL = 5
DOWNLOAD_TIMEOUT = 60
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


def get_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return MIME_TYPES.get(ext, "application/octet-stream")


def extract_clean_text_from_zip(zip_bytes: bytes) -> Tuple[str, List[str]]:
    """
    Extract pure document text from Sarvam's output ZIP.
    Prioritizes metadata/page_*.json to get raw text blocks with zero HTML/CSS pollution.
    Falls back to parsed HTML (with style/script tags decomposed) or Markdown if needed.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        file_list = archive.namelist()

        # 1. Primary Strategy: Extract blocks directly from metadata/page_*.json
        metadata_files = [
            f for f in file_list
            if "metadata" in f and f.lower().endswith(".json")
        ]

        if metadata_files:
            # Sort pages numerically if possible (e.g. page_001.json)
            metadata_files.sort()
            pages_text = []

            for meta_file in metadata_files:
                try:
                    data = json.loads(archive.read(meta_file).decode("utf-8", errors="replace"))
                    blocks = data.get("blocks", [])
                    # Sort blocks by reading order if available
                    blocks.sort(key=lambda b: b.get("reading_order", 0))
                    page_texts = [
                        b["text"].strip()
                        for b in blocks
                        if isinstance(b.get("text"), str) and b["text"].strip()
                    ]
                    if page_texts:
                        pages_text.append("\n\n".join(page_texts))
                except Exception as e:
                    logger.warning(f"Error parsing metadata file {meta_file}: {e}")

            if pages_text:
                full_text = "\n\n".join(pages_text).strip()
                if full_text:
                    return full_text, file_list

        # 2. Secondary Strategy: Markdown file if present
        md_files = [f for f in file_list if f.lower().endswith(".md")]
        if md_files:
            md_text = archive.read(md_files[0]).decode("utf-8", errors="replace").strip()
            # Clean markdown of any residual HTML style/script blocks
            md_text = re.sub(r"<style[\s\S]*?</style>", "", md_text, flags=re.IGNORECASE)
            md_text = re.sub(r"<script[\s\S]*?</script>", "", md_text, flags=re.IGNORECASE)
            if md_text:
                return md_text.strip(), file_list

        # 3. Tertiary Strategy: Parse HTML safely with BeautifulSoup (decomposing CSS/JS)
        html_files = [
            f for f in file_list
            if f.lower().endswith((".html", ".htm"))
        ]
        if html_files:
            raw_html = archive.read(html_files[0]).decode("utf-8", errors="replace")
            soup = BeautifulSoup(raw_html, "html.parser")
            for tag in soup(["style", "script", "head", "noscript", "svg", "meta", "link"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            cleaned_html_text = "\n".join(lines).strip()
            if cleaned_html_text:
                return cleaned_html_text, file_list

        raise ValueError(f"No usable text content found in ZIP. Available files: {file_list}")


def try_identify_language_sarvam(client: SarvamAI, text: str) -> Optional[str]:
    """
    Attempt to use Sarvam Language Identification API if available under subscription policy.
    Never fails the pipeline if the endpoint is restricted (e.g. 403 Forbidden).
    """
    if not text or not text.strip():
        return None

    sample = text[:900].strip()
    try:
        response = client.text.identify_language(input=sample)
        code = getattr(response, "language_code", None)
        if code:
            return str(code)
    except Exception as e:
        logger.debug(f"Sarvam Language Identification API skipped or restricted: {e}")

    return None


class SarvamOCR:
    """
    Language-independent Document AI OCR processor.
    Handles single-pass multilingual extraction across Indian languages and English.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SARVAM_API_KEY")
        if not self.api_key:
            raise ValueError("SARVAM_API_KEY is not configured.")
        self.client = SarvamAI(api_subscription_key=self.api_key)

    def _execute_parse_job(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        language: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Submit a parse job and wait for terminal status.
        When language is None, language is omitted so Sarvam uses its Indic default model.
        """
        kwargs = {
            "file": [(filename, io.BytesIO(file_bytes), mime_type)],
            "content_type": "mixed",
        }
        # Only pass language if explicitly provided (e.g. "en-IN" fallback)
        if language:
            kwargs["language"] = language

        job = self.client.doc_ai.parse(**kwargs)
        job_id = getattr(job, "job_id", None)
        if not job_id:
            raise RuntimeError("Sarvam Document AI did not return a job_id.")

        start_time = time.time()
        interval = INITIAL_POLL_INTERVAL

        while True:
            status_res = self.client.doc_ai.get_status(job_id=job_id)
            raw_status = str(getattr(status_res, "status", "")).lower().split(".")[-1]
            raw_status = raw_status.replace(" ", "_")

            if raw_status in {"completed", "partially_completed", "failed", "rejected"}:
                break

            if time.time() - start_time > MAX_WAIT_SECONDS:
                raise TimeoutError(f"Sarvam OCR timed out for job ID: {job_id}")

            time.sleep(interval)
            interval = min(interval + 1, MAX_POLL_INTERVAL)

        if raw_status not in {"completed", "partially_completed"}:
            raise RuntimeError(f"Sarvam OCR ended with terminal status: {raw_status}")

        return job_id, raw_status

    def _download_and_extract(self, job_id: str) -> Tuple[str, List[str]]:
        dl_info = self.client.doc_ai.get_download_url(job_id=job_id)
        dl_url = getattr(dl_info, "url", None)
        if not dl_url:
            raise RuntimeError(f"Sarvam did not return a download URL for job {job_id}")

        resp = requests.get(dl_url, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()

        return extract_clean_text_from_zip(resp.content)

    def process_document(
        self,
        file_bytes: bytes,
        filename: str = "document.jpg",
        mime_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a document using Sarvam Document AI without requiring manual language selection.
        - Primary pass: Indic default (language omitted), covering 22 Indian languages and mixed text.
        - Controlled fallback: if primary extraction is empty, retries once with 'en-IN'.
        Returns structured dictionary.
        """
        if not file_bytes:
            raise ValueError("Input file_bytes is empty.")

        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"File size exceeds maximum allowed limit of {MAX_FILE_SIZE_BYTES // (1024*1024)} MB.")

        if not mime_type:
            mime_type = get_mime_type(filename)

        errors: List[str] = []
        extracted_text = ""
        job_id = None
        pass_count = 1
        start_time = time.time()

        # Step 1: Primary Pass (Language Omitted -> Indic Default)
        try:
            job_id, status = self._execute_parse_job(
                file_bytes=file_bytes,
                filename=filename,
                mime_type=mime_type,
                language=None  # Triggers Sarvam's native Indic default engine
            )
            extracted_text, _ = self._download_and_extract(job_id)
        except Exception as e:
            msg = f"Primary Indic OCR pass failed: {e}"
            logger.warning(msg)
            errors.append(msg)

        # Step 2: Controlled Fallback (Only if primary produced empty text)
        if not extracted_text.strip():
            logger.info("Primary pass returned empty text. Attempting controlled fallback using en-IN...")
            pass_count += 1
            try:
                fallback_job_id, fallback_status = self._execute_parse_job(
                    file_bytes=file_bytes,
                    filename=filename,
                    mime_type=mime_type,
                    language="en-IN"
                )
                fallback_text, _ = self._download_and_extract(fallback_job_id)
                if fallback_text.strip():
                    extracted_text = fallback_text
                    job_id = fallback_job_id
            except Exception as e:
                msg = f"Fallback en-IN OCR pass failed: {e}"
                logger.warning(msg)
                errors.append(msg)

        # Step 3: Script Detection
        script_info = detect_scripts(extracted_text)
        detected_scripts = script_info["scripts"]
        total_chars = script_info["total_characters"]

        # Step 4: Optional Sarvam Language Identification API
        lid_code = try_identify_language_sarvam(self.client, extracted_text)

        # Step 5: Map Scripts to Candidate Languages
        detected_languages = map_scripts_to_languages(
            detected_scripts=detected_scripts,
            lid_language_code=lid_code
        )

        # Review requirement heuristic:
        # Flag review if total alphabetic chars < 10, or text is completely empty, or errors occurred
        review_required = total_chars < 10 or bool(errors) or not extracted_text.strip()

        duration = round(time.time() - start_time, 2)

        return {
            "extracted_text": extracted_text,
            "detected_languages": detected_languages,
            "detected_scripts": detected_scripts,
            "language_confidence": None,  # Sarvam does not provide numeric confidence in LID or Parse
            "review_required": review_required,
            "errors": errors,
            "metadata": {
                "job_id": job_id,
                "filename": filename,
                "duration_seconds": duration,
                "passes_performed": pass_count,
                "lid_code": lid_code,
                "total_characters": total_chars,
            }
        }
