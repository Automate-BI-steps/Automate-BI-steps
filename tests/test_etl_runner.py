from src.agents.etl.runner import invoke_with_retry

BAD_SCORE = "The returned Candidate Score does not match the required formula."


def _invoker(results):
    calls = []

    def invoke(state):
        calls.append(state)
        return results[min(len(calls), len(results)) - 1]

    return invoke, calls


def test_reussit_au_deuxieme_essai():
    invoke, calls = _invoker([{"fact_reasoning_error": BAD_SCORE}, {"fact_reasoning_error": None, "ok": 1}])
    result, errors = invoke_with_retry(invoke, {}, max_attempts=3)
    assert result["ok"] == 1 and errors == [BAD_SCORE] and len(calls) == 2


def test_epuise_les_essais():
    invoke, calls = _invoker([{"fact_reasoning_error": BAD_SCORE}])
    result, errors = invoke_with_retry(invoke, {}, max_attempts=3)
    assert result["fact_reasoning_error"] == BAD_SCORE and len(errors) == 3 and len(calls) == 3


def test_pas_de_nouvel_essai_sans_cle():
    invoke, calls = _invoker([{"fact_reasoning_error": "OLLAMA_API_KEY is not set. Configure your Ollama Cloud API key."}])
    _, errors = invoke_with_retry(invoke, {}, max_attempts=3)
    assert len(errors) == 1 and len(calls) == 1
