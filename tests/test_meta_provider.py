import io
import json
import urllib.request

from src.agents.dashboard.dashboard_planner import MetaResponsesProvider
from src.agents.dashboard.config import GeneratorConfig, OperatingMode


def test_meta_configuration_uses_model_api_key_and_ai_mode(monkeypatch):
    monkeypatch.setenv("BI_FLOW_LLM_PROVIDER", "meta")
    monkeypatch.setenv("MODEL_API_KEY", "configured-secret")
    monkeypatch.delenv("META_API_KEY", raising=False)
    config = GeneratorConfig.from_env()
    assert config.api_key == "configured-secret"
    assert config.mode is OperatingMode.AI_ASSISTED


def test_llm7_configuration_uses_openai_compatible_defaults(monkeypatch):
    monkeypatch.setenv("BI_FLOW_LLM_PROVIDER", "llm7")
    monkeypatch.setenv("MODEL_API_KEY", "configured-secret")
    config = GeneratorConfig.from_env()
    assert config.api_key == "configured-secret"
    assert config.llm_model == "default"
    assert config.api_base_url == "https://api.llm7.io/v1"
    assert config.mode is OperatingMode.AI_ASSISTED


class _Response:
    def __iter__(self):
        return iter([
            b"event: response.output_text.delta\n",
            b'data: {"type":"response.output_text.delta","delta":"{\\"ok\\":"}\n',
            b'data: {"type":"response.output_text.delta","delta":"true}"}\n',
            b"data: [DONE]\n",
        ])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_meta_provider_parses_stream_without_exposing_key(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = MetaResponsesProvider(api_key="secret", timeout=7)
    assert provider.generate("prompt", "system") == '{"ok":true}'
    assert captured["request"].full_url == "https://api.meta.ai/v1/responses"
    payload = json.loads(captured["request"].data)
    assert payload["model"] == "muse-wonder-1.0"
    assert payload["stream"] is True
    assert payload["instructions"] == "system"
    assert captured["request"].headers["Authorization"] == "Bearer secret"

