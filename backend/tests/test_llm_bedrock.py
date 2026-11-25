import json
import botocore.exceptions
import pytest

from app import aws, llm


class FakeBedrock:
    def __init__(self):
        self.configs = []

    def converse(self, **kwargs):
        self.configs.append(kwargs["inferenceConfig"])
        if "temperature" in kwargs["inferenceConfig"]:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "ValidationException",
                           "Message": "The model returned the following errors: `temperature` is deprecated for this model."}},
                "Converse",
            )
        return {"output": {"message": {"content": [{"text": '{"ok": true}'}]}}}


@pytest.fixture
def fake(monkeypatch):
    client = FakeBedrock()
    monkeypatch.setattr(aws, "bedrock_runtime", lambda: client)
    monkeypatch.setattr(llm, "_NO_TEMPERATURE_MODELS", set())
    return client


def test_model_rejecting_temperature_is_retried_and_remembered(fake):
    first = llm.bedrock_converse("m-new", [{"role": "user", "content": [{"text": "hi"}]}], 50, 0.4)
    assert first["output"]["message"]["content"][0]["text"] == '{"ok": true}'
    llm.bedrock_converse("m-new", [{"role": "user", "content": [{"text": "hi"}]}], 50, 0.4)
    assert fake.configs == [{"maxTokens": 50, "temperature": 0.4}, {"maxTokens": 50}, {"maxTokens": 50}]


def test_other_validation_errors_are_not_swallowed(monkeypatch, fake):
    def boom(**_):
        raise botocore.exceptions.ClientError({"Error": {"Code": "ValidationException", "Message": "bad input"}}, "Converse")

    fake.converse = boom
    with pytest.raises(botocore.exceptions.ClientError):
        llm.bedrock_converse("m-other", [], 50, 0.0)


@pytest.mark.parametrize("reply", [
    '{"found": true, "answer": "x"}\n{"found": false}',             # a second object after the first
    '{"found": true, "answer": "x"}\nNote: see {source 0}.',         # trailing text with braces
    'Here you go:\n```json\n{"found": true, "answer": "x"}\n```\n{}',  # fenced, then junk
    'Sure. {"found": true, "answer": "x"}',                          # preamble
])
def test_extract_json_takes_first_complete_object(reply):
    assert json.loads(llm.extract_json(reply)) == {"found": True, "answer": "x"}


def test_extract_json_keeps_braces_inside_strings_and_unicode():
    reply = '{"quote": "softmax({QK^T})", "answer": "Điều 3"} trailing }'
    assert json.loads(llm.extract_json(reply)) == {"quote": "softmax({QK^T})", "answer": "Điều 3"}


def test_extract_json_without_object():
    assert llm.extract_json("no json here") == "{}"
