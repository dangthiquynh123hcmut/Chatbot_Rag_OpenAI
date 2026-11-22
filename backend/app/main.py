from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .converters import SUPPORTED_EXTENSIONS
from .db import close_pool, connection, init_db
from .ingest import create_document, process_document
from .rag import answer_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    close_pool()


app = FastAPI(title="Hierarchy-Aware RAG", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    document_ids: list[uuid.UUID] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


DOC_COLS = "id, filename, file_type, title, status, error, num_sections, num_chunks, created_at"


def _upload_path(doc_id: uuid.UUID, filename: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", Path(filename).name, flags=re.UNICODE)
    return get_settings().upload_dir / f"{doc_id}_{safe}"


def _stored_file(doc_id: uuid.UUID) -> Path | None:
    matches = list(get_settings().upload_dir.glob(f"{doc_id}_*"))
    return matches[0] if matches else None


@app.get("/api/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    s = get_settings()
    return {
        "status": "ok",
        "provider": s.llm_provider,
        "llm_model": s.active_llm_model,
        "embedding_model": s.active_embedding_model,
        "embedding_dim": s.embedding_dim,
        "reranker": s.reranker,
        "aws_region": s.aws_region if s.llm_provider == "bedrock" else None,
    }


@app.post("/api/documents")
async def upload_documents(background: BackgroundTasks, files: list[UploadFile] = File(...)):
    created = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"File '{f.filename}' không được hỗ trợ. Chỉ nhận PDF, DOCX.")
    for f in files:
        doc_id = await run_in_threadpool(create_document, f.filename)
        path = _upload_path(doc_id, f.filename)
        with open(path, "wb") as out:
            while chunk := await f.read(1024 * 1024):
                out.write(chunk)
        background.add_task(process_document, doc_id, path)
        created.append({"id": str(doc_id), "filename": f.filename, "status": "processing"})
    return {"documents": created}


@app.get("/api/documents")
def list_documents():
    with connection() as conn:
        rows = conn.execute(f"SELECT {DOC_COLS} FROM documents ORDER BY created_at DESC").fetchall()
    return {"documents": rows}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: uuid.UUID):
    with connection() as conn:
        row = conn.execute(f"SELECT {DOC_COLS} FROM documents WHERE id=%s", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return row


@app.get("/api/documents/{doc_id}/markdown", response_class=PlainTextResponse)
def get_markdown(doc_id: uuid.UUID):
    with connection() as conn:
        row = conn.execute("SELECT markdown FROM documents WHERE id=%s", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return row["markdown"] or ""


@app.get("/api/documents/{doc_id}/tree")
def get_tree(doc_id: uuid.UUID):
    with connection() as conn:
        rows = conn.execute(
            """SELECT id, parent_id, depth, kind, number, label, title, heading, refs
               FROM sections WHERE document_id=%s AND kind <> 'document' ORDER BY ord""",
            (doc_id,),
        ).fetchall()
    return {"sections": rows}


@app.get("/api/sections/{section_id}")
def get_section(section_id: uuid.UUID):
    with connection() as conn:
        row = conn.execute(
            """SELECT s.id, s.document_id, d.title AS document_title, s.kind, s.label, s.title, s.heading,
                      s.path, s.refs, s.full_md
               FROM sections s JOIN documents d ON d.id = s.document_id WHERE s.id=%s""",
            (section_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy mục")
    return row


@app.post("/api/documents/{doc_id}/reindex")
def reindex_document(doc_id: uuid.UUID, background: BackgroundTasks):
    path = _stored_file(doc_id)
    if path is None:
        raise HTTPException(404, "Không còn file gốc để xử lý lại")
    with connection() as conn:
        conn.execute("UPDATE documents SET status='processing', error=NULL WHERE id=%s", (doc_id,))
    background.add_task(process_document, doc_id, path)
    return {"id": str(doc_id), "status": "processing"}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: uuid.UUID):
    with connection() as conn:
        deleted = conn.execute("DELETE FROM documents WHERE id=%s RETURNING id", (doc_id,)).fetchone()
    if not deleted:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    path = _stored_file(doc_id)
    if path:
        path.unlink(missing_ok=True)
    (get_settings().markdown_dir / f"{doc_id}.md").unlink(missing_ok=True)
    return {"deleted": str(doc_id)}


@app.post("/api/query")
def query(req: QueryRequest):
    try:
        return answer_query(req.query, req.document_ids, req.top_k)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
