from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .auth import get_current_user, require_admin
from .auth import router as auth_router
from .chat import router as chat_router
from .config import get_settings
from .converters import SUPPORTED_EXTENSIONS
from .docling_pdf import figure_path, remove_figures
from .db import close_pool, connection, get_session, init_db
from .ingest import create_document, process_document
from .metadata import DocMeta
from .models import Document
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(chat_router)


class MetadataUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    short_title: str | None = Field(default=None, max_length=200)
    authors: list[str] | None = Field(default=None, max_length=50)
    year: int | None = Field(default=None, ge=1400, le=2100)
    doc_type: str | None = None
    publisher: str | None = Field(default=None, max_length=300)
    isbn: str | None = Field(default=None, max_length=30)
    aliases: list[str] | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=1000)


class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    document_ids: list[uuid.UUID] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


def _upload_path(doc_id: uuid.UUID, filename: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", Path(filename).name, flags=re.UNICODE)
    return get_settings().upload_dir / f"{doc_id}_{safe}"


def _stored_file(doc_id: uuid.UUID) -> Path | None:
    matches = list(get_settings().upload_dir.glob(f"{doc_id}_*"))
    return matches[0] if matches else None


def _document(db: Session, doc_id: uuid.UUID, lock: bool = False) -> Document:
    doc = db.get(Document, doc_id, with_for_update=lock)
    if doc is None:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return doc


@app.get("/api/health")
def health(db: Session = Depends(get_session)):
    db.execute(text("SELECT 1"))
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
async def upload_documents(
    background: BackgroundTasks, files: list[UploadFile] = File(...), _: dict = Depends(require_admin)
):
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
def list_documents(_: dict = Depends(get_current_user), db: Session = Depends(get_session)):
    docs = db.scalars(select(Document).order_by(Document.created_at.desc())).all()
    return {"documents": [d.to_dict() for d in docs]}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: uuid.UUID, _: dict = Depends(get_current_user), db: Session = Depends(get_session)):
    return _document(db, doc_id).to_dict()


@app.get("/api/documents/{doc_id}/markdown", response_class=PlainTextResponse)
def get_markdown(doc_id: uuid.UUID, _: dict = Depends(get_current_user), db: Session = Depends(get_session)):
    return _document(db, doc_id).markdown or ""


@app.get("/api/documents/{doc_id}/tree")
def get_tree(doc_id: uuid.UUID, _: dict = Depends(get_current_user)):
    with connection() as conn:
        rows = conn.execute(
            """SELECT id, parent_id, depth, kind, label, title, refs
               FROM sections WHERE document_id=%s AND kind <> 'document' ORDER BY ord""",
            (doc_id,),
        ).fetchall()
    return {"sections": rows}


@app.get("/api/sections/{section_id}")
def get_section(section_id: uuid.UUID, _: dict = Depends(get_current_user)):
    with connection() as conn:
        row = conn.execute(
            """SELECT s.id, s.document_id, d.title AS document_title, s.kind, s.label, s.title,
                      s.path, s.refs, s.full_md
               FROM sections s JOIN documents d ON d.id = s.document_id WHERE s.id=%s""",
            (section_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy mục")
    return row


@app.patch("/api/documents/{doc_id}/metadata")
def update_metadata(
    doc_id: uuid.UUID, req: MetadataUpdate, _: dict = Depends(require_admin), db: Session = Depends(get_session)
):
    doc = _document(db, doc_id, lock=True)
    merged = {**DocMeta.from_dict(doc.meta).to_dict(), **req.model_dump(exclude_unset=True), "source": "manual"}
    meta = DocMeta.from_dict(merged)
    doc.meta = meta.to_dict()
    doc.title = meta.title or doc.title
    db.flush()
    return {"document": doc.to_dict(), "reindex_recommended": True}


@app.post("/api/documents/{doc_id}/reindex")
def reindex_document(
    doc_id: uuid.UUID,
    background: BackgroundTasks,
    _: dict = Depends(require_admin),
    db: Session = Depends(get_session),
):
    path = _stored_file(doc_id)
    if path is None:
        raise HTTPException(404, "Không còn file gốc để xử lý lại")
    doc = _document(db, doc_id, lock=True)
    doc.status, doc.error = "processing", None
    db.commit()
    background.add_task(process_document, doc_id, path)
    return {"id": str(doc_id), "status": "processing"}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: uuid.UUID, _: dict = Depends(require_admin), db: Session = Depends(get_session)):
    db.delete(_document(db, doc_id))
    db.commit()
    path = _stored_file(doc_id)
    if path:
        path.unlink(missing_ok=True)
    (get_settings().markdown_dir / f"{doc_id}.md").unlink(missing_ok=True)
    remove_figures(str(doc_id))
    return {"deleted": str(doc_id)}


@app.get("/api/documents/{doc_id}/figures/{name}")
def get_figure(doc_id: uuid.UUID, name: str, _: dict = Depends(get_current_user)):
    path = figure_path(str(doc_id), name)
    if path is None:
        raise HTTPException(404, "Không tìm thấy hình")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/query")
def query(req: QueryRequest, _: dict = Depends(get_current_user)):
    try:
        return answer_query(req.query, req.document_ids, req.top_k)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
