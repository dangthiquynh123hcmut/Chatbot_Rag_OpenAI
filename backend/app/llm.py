from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np

from .config import get_settings

log = logging.getLogger(__name__)

JSON_ONLY_SUFFIX = "\n\nChỉ trả về đúng một đối tượng JSON hợp lệ, không kèm giải thích hay markdown."
NETWORK_BACKOFF_S = (2, 4, 8, 16, 30)
TRANSIENT_ERRORS = {
    "EndpointConnectionError",
    "ConnectionClosedError",
    "ReadTimeoutError",
    "ConnectTimeoutError",
    "APIConnectionError",
    "APITimeoutError",
}


def is_transient_network_error(exc: Exception) -> bool:
    return any(cls.__name__ in TRANSIENT_ERRORS for cls in type(exc).__mro__)


def with_network_retry(fn, *args, **kwargs):
    attempts = len(NETWORK_BACKOFF_S) + 1
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == attempts - 1 or not is_transient_network_error(exc):
                raise
            delay = NETWORK_BACKOFF_S[attempt]
            log.warning("Network error (%s), retry %d/%d in %ss", type(exc).__name__, attempt + 1, attempts - 1, delay)
            time.sleep(delay)


@lru_cache
def openai_client():
    from openai import OpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("Thiếu OPENAI_API_KEY trong file .env (hoặc đặt LLM_PROVIDER=bedrock)")
    return OpenAI(api_key=settings.openai_api_key)


def _openai_embed(texts: list[str], batch_size: int = 64) -> list[np.ndarray]:
    settings = get_settings()
    out: list[np.ndarray] = []
    for i in range(0, len(texts), batch_size):
        batch = [t[:24000] for t in texts[i : i + batch_size]]
        resp = with_network_retry(
            openai_client().embeddings.create,
            model=settings.embedding_model,
            input=batch,
            dimensions=settings.embedding_dim,
        )
        out.extend(np.array(d.embedding, dtype=np.float32) for d in resp.data)
    return out


def _titan_embed_one(text: str) -> np.ndarray:
    from .aws import bedrock_runtime

    settings = get_settings()
    body = {"inputText": text[:30000], "dimensions": settings.embedding_dim, "normalize": True}
    resp = with_network_retry(
        bedrock_runtime().invoke_model,
        modelId=settings.bedrock_embedding_model,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    data = json.loads(resp["body"].read())
    return np.array(data["embedding"], dtype=np.float32)


def _cohere_embed(texts: list[str], input_type: str) -> list[np.ndarray]:
    from .aws import bedrock_runtime

    settings = get_settings()
    model = settings.bedrock_embedding_model
    out: list[np.ndarray] = []
    for i in range(0, len(texts), 96):
        body: dict = {
            "texts": [t[:2048] for t in texts[i : i + 96]],
            "input_type": input_type,
            "truncate": "END",
        }
        if "embed-v4" in model:
            body["embedding_types"] = ["float"]
            body["output_dimension"] = settings.embedding_dim
        resp = with_network_retry(
            bedrock_runtime().invoke_model, modelId=model, body=json.dumps(body), contentType="application/json", accept="application/json"
        )
        data = json.loads(resp["body"].read())
        vectors = data["embeddings"]
        if isinstance(vectors, dict):
            vectors = vectors["float"]
        out.extend(np.array(v, dtype=np.float32) for v in vectors)
    return out


def _bedrock_embed(texts: list[str], input_type: str) -> list[np.ndarray]:
    settings = get_settings()
    if settings.bedrock_embedding_model.startswith("cohere.embed"):
        return _cohere_embed(texts, input_type)
    if len(texts) == 1:
        return [_titan_embed_one(texts[0])]
    with ThreadPoolExecutor(max_workers=settings.bedrock_max_workers) as pool:
        return list(pool.map(_titan_embed_one, texts))


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    if get_settings().llm_provider == "bedrock":
        return _bedrock_embed(texts, "search_document")
    return _openai_embed(texts)


def embed_query(text: str) -> np.ndarray:
    if get_settings().llm_provider == "bedrock":
        return _bedrock_embed([text], "search_query")[0]
    return _openai_embed([text])[0]


def extract_json(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return "{}"


def _bedrock_chat_json(system: str, user: str, temperature: float, model: str | None) -> str:
    from .aws import bedrock_runtime

    settings = get_settings()
    resp = with_network_retry(
        bedrock_runtime().converse,
        modelId=model or settings.bedrock_llm_model,
        system=[{"text": system + JSON_ONLY_SUFFIX}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": settings.llm_max_tokens, "temperature": temperature},
    )
    parts = resp["output"]["message"]["content"]
    text = "".join(p.get("text", "") for p in parts)
    return extract_json(text)


def _openai_chat_json(system: str, user: str, temperature: float, model: str | None) -> str:
    settings = get_settings()
    resp = with_network_retry(
        openai_client().chat.completions.create,
        model=model or settings.llm_model,
        temperature=temperature,
        max_tokens=settings.llm_max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or "{}"


def chat_json(system: str, user: str, temperature: float = 0.0, model: str | None = None) -> str:
    if get_settings().llm_provider == "bedrock":
        return _bedrock_chat_json(system, user, temperature, model)
    return _openai_chat_json(system, user, temperature, model)


_UNAVAILABLE_MODELS: set[str] = set()
_UNAVAILABLE_LOCK = threading.Lock()
UNAVAILABLE_ERROR_CODES = {"AccessDeniedException", "ResourceNotFoundException", "model_not_found", "NotFoundError"}


def _is_model_unavailable(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
    if code in UNAVAILABLE_ERROR_CODES:
        return True
    return type(exc).__name__ in UNAVAILABLE_ERROR_CODES or getattr(exc, "code", None) in UNAVAILABLE_ERROR_CODES


def effective_judge_model() -> str:
    s = get_settings()
    return s.active_llm_model if s.judge_model in _UNAVAILABLE_MODELS else s.judge_model


def judge_chat_json(system: str, user: str, temperature: float = 0.0) -> str:
    model = effective_judge_model()
    try:
        return chat_json(system, user, temperature, model)
    except Exception as exc:
        fallback = get_settings().active_llm_model
        if model == fallback or not _is_model_unavailable(exc):
            raise
        with _UNAVAILABLE_LOCK:
            if model not in _UNAVAILABLE_MODELS:
                _UNAVAILABLE_MODELS.add(model)
                log.warning("Judge model %s unavailable (%s); falling back to %s", model, exc, fallback)
        return chat_json(system, user, temperature, fallback)
