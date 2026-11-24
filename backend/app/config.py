from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
TITAN_V2_DIMS = {256, 512, 1024}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "openai"

    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 1536

    aws_profile: str = ""
    aws_region: str = "ap-southeast-2"
    bedrock_llm_model: str = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_embedding_model: str = "amazon.titan-embed-text-v2:0"
    bedrock_rerank_region: str = "ap-northeast-1"
    bedrock_rerank_model: str = "cohere.rerank-v3-5:0"
    bedrock_max_workers: int = 8
    llm_max_tokens: int = 2048

    eval_judge_model: str = ""
    eval_judge_model_bedrock: str = "au.anthropic.claude-sonnet-5"
    eval_judge_model_openai: str = "gpt-4o"

    database_url: str = "postgresql://rag:rag@localhost:5432/rag"

    chunk_max_chars: int = 2000
    chunk_overlap_paragraphs: int = 1

    vector_top_k: int = 30
    keyword_top_k: int = 30
    rerank_candidates: int = 20
    final_top_k: int = 5
    reranker: str = "llm"
    cross_encoder_model: str = "BAAI/bge-reranker-v2-m3"

    upload_dir: Path = BACKEND_DIR / "storage" / "uploads"
    markdown_dir: Path = BACKEND_DIR / "storage" / "markdown"
    enable_ocr: bool = True
    ocr_language: str = "eng"

    cors_origins: str = "http://localhost:5173"

    app_env: str = "development"
    jwt_secret: str = ""
    jwt_expire_minutes: int = 15
    refresh_token_days: int = 14
    refresh_reuse_grace_seconds: int = 30
    cookie_secure: bool = False
    allow_registration: bool = True
    min_password_length: int = 8

    metadata_llm: bool = True

    pdf_parser: str = "docling"
    docling_artifacts_path: str = ""
    docling_table_mode: str = "fast"
    docling_threads: int = 8
    formula_vlm: bool = True
    figure_descriptions: bool = True
    max_figure_descriptions: int = 40
    min_figure_pixels: int = 120
    vision_model: str = ""
    figure_dir: Path = BACKEND_DIR / "storage" / "figures"

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        self.llm_provider = self.llm_provider.strip().lower()
        self.reranker = self.reranker.strip().lower()
        self.app_env = self.app_env.strip().lower()
        if not self.is_development and len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET (tối thiểu 32 ký tự) là bắt buộc khi APP_ENV khác 'development'")
        if self.llm_provider not in {"openai", "bedrock"}:
            raise ValueError("LLM_PROVIDER phải là 'openai' hoặc 'bedrock'")
        if self.reranker not in {"llm", "bedrock", "cross_encoder", "none"}:
            raise ValueError("RERANKER phải là llm | bedrock | cross_encoder | none")
        if (
            self.llm_provider == "bedrock"
            and self.bedrock_embedding_model.startswith("amazon.titan-embed-text-v2")
            and self.embedding_dim not in TITAN_V2_DIMS
        ):
            raise ValueError("Titan Text Embeddings V2 chỉ hỗ trợ EMBEDDING_DIM = 256, 512 hoặc 1024")
        return self

    @property
    def is_development(self) -> bool:
        return self.app_env in {"development", "dev", "local", "test"}

    @property
    def active_llm_model(self) -> str:
        return self.bedrock_llm_model if self.llm_provider == "bedrock" else self.llm_model

    @property
    def judge_model(self) -> str:
        if self.eval_judge_model:
            return self.eval_judge_model
        return self.eval_judge_model_bedrock if self.llm_provider == "bedrock" else self.eval_judge_model_openai

    @property
    def active_embedding_model(self) -> str:
        return self.bedrock_embedding_model if self.llm_provider == "bedrock" else self.embedding_model


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    s.markdown_dir.mkdir(parents=True, exist_ok=True)
    s.figure_dir.mkdir(parents=True, exist_ok=True)
    return s
