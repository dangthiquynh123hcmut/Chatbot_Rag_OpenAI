"""Where the uploaded files and the generated markdown of a document are stored."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.config import get_settings


def upload_path(document_id: uuid.UUID, filename: str) -> Path:
    """Path of the original file of a document: <upload_dir>/<id>_<safe filename>."""
    safe = re.sub(r"[^\w.\-]+", "_", Path(filename).name, flags=re.UNICODE)
    return get_settings().upload_dir / f"{document_id}_{safe}"


def stored_file(document_id: uuid.UUID) -> Path | None:
    """The original file of a document, if it is still stored."""
    matches = list(get_settings().upload_dir.glob(f"{document_id}_*"))
    return matches[0] if matches else None


def markdown_path(document_id: uuid.UUID) -> Path:
    """Canonical markdown written by the ingestion."""
    return get_settings().markdown_dir / f"{document_id}.md"
