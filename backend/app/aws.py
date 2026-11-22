from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.config import Config

from .config import get_settings

RETRY_CONFIG = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=120,
    max_pool_connections=32,
)


@lru_cache
def session() -> boto3.Session:
    s = get_settings()
    return boto3.Session(profile_name=s.aws_profile or None, region_name=s.aws_region)


@lru_cache
def bedrock_runtime():
    return session().client("bedrock-runtime", config=RETRY_CONFIG)


@lru_cache
def bedrock_agent_runtime():
    return session().client(
        "bedrock-agent-runtime",
        region_name=get_settings().bedrock_rerank_region,
        config=RETRY_CONFIG,
    )


def caller_identity() -> dict:
    ident = session().client("sts").get_caller_identity()
    return {"account": ident["Account"], "arn": ident["Arn"]}


def rerank_model_arn() -> str:
    s = get_settings()
    return f"arn:aws:bedrock:{s.bedrock_rerank_region}::foundation-model/{s.bedrock_rerank_model}"
