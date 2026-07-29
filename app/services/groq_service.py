"""Groq model discovery and OpenAI-compatible chat completions."""

import json
import re

import requests


DEFAULT_API_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqError(Exception):
    """A safe, user-facing Groq configuration, request, or response error."""


def _api_key(config):
    key = str(config.get("GROQ_API_KEY") or "").strip()
    if not key:
        raise GroqError("Groq is not configured. Set GROQ_API_KEY on the API server.")
    return key


def _base_url(config):
    return str(config.get("GROQ_API_URL") or DEFAULT_API_BASE_URL).strip().rstrip("/")


def _headers(config):
    return {
        "Authorization": f"Bearer {_api_key(config)}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _error_message(response):
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or response.reason)
            return str(payload.get("message") or response.reason)
    except (ValueError, AttributeError):
        pass
    return f"Groq returned HTTP {response.status_code}."


def _json_from_content(content):
    if isinstance(content, list):
        content = "".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _models_response(config):
    try:
        response = requests.get(
            f"{_base_url(config)}/models",
            headers=_headers(config),
            timeout=(10, max(5, int(config.get("GROQ_REQUEST_TIMEOUT", 20)))),
        )
    except requests.RequestException as err:
        raise GroqError("Groq could not be reached while loading available models.") from err

    if not response.ok:
        raise GroqError(f"Groq could not load available models: {_error_message(response)}")
    return response


def _parse_models(response):
    records = response.json().get("data", [])
    if not isinstance(records, list):
        raise TypeError
    models = []
    for record in records:
        model_id = str(record.get("id") or "").strip()
        lowered_id = model_id.lower()
        if not model_id or record.get("active", True) is False:
            continue
        # These models are for audio or moderation, not chat-completion
        # prompts used by book creation and pronunciation scoring.
        if any(marker in lowered_id for marker in ("whisper", "guard", "tts")):
            continue
        models.append(
            {
                "id": model_id,
                "owned_by": str(record.get("owned_by") or "Groq"),
                "context_window": record.get("context_window"),
            }
        )
    return sorted(models, key=lambda model: model["id"].lower())


def list_models(config):
    """Return the currently active Groq chat-capable model metadata."""
    response = _models_response(config)

    try:
        return _parse_models(response)
    except (AttributeError, TypeError, ValueError) as err:
        raise GroqError("Groq returned an invalid model list.") from err

def _model(config, requested_model):
    model = str(requested_model or config.get("GROQ_MODEL") or DEFAULT_MODEL).strip()
    if not model or len(model) > 200:
        raise GroqError("Choose a valid Groq model.")
    return model


def _chat_completion(messages, requested_model, config, *, temperature, max_tokens):
    model = _model(config, requested_model)
    try:
        response = requests.post(
            f"{_base_url(config)}/chat/completions",
            headers=_headers(config),
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
            timeout=(10, max(10, int(config.get("GROQ_REQUEST_TIMEOUT", 60)))),
        )
    except requests.RequestException as err:
        raise GroqError("Groq could not be reached while processing the request.") from err

    if not response.ok:
        raise GroqError(f"Groq request failed: {_error_message(response)}")
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as err:
        raise GroqError("Groq returned an unexpected response.") from err


def generate_book_draft(age_group, reading_level, idea, config, model=None):
    """Generate a structured children's book draft through Groq."""
    idea = str(idea or "").strip()
    if not idea:
        raise GroqError("A story idea is required.")

    prompt = f"""
Write an original children's story for the TeachAlike reading app.

Age group: {str(age_group or '').strip()}
Reading level: {str(reading_level or '').strip()}
Story idea: {idea[:500]}

Return a short, engaging story with a clear beginning, middle, and ending.
Use warm, age-appropriate language, vivid but safe imagery, and short paragraphs.
Do not include a preface, lesson-plan notes, markdown headings, or questions.
Return ONLY valid JSON with exactly these string fields:
{{"title": "Story title", "text_content": "The complete story text"}}
Do not wrap the JSON in markdown fences.
"""
    content = _chat_completion(
        [
            {
                "role": "system",
                "content": "You create safe, original children's books and follow JSON output instructions exactly.",
            },
            {"role": "user", "content": prompt},
        ],
        model,
        config,
        temperature=0.8,
        max_tokens=2200,
    )
    try:
        generated = _json_from_content(content)
    except (TypeError, ValueError) as err:
        raise GroqError("Groq returned book content in an unexpected format.") from err

    title = str(generated.get("title") or "").strip() if isinstance(generated, dict) else ""
    text_content = str(generated.get("text_content") or "").strip() if isinstance(generated, dict) else ""
    if not title or not text_content:
        raise GroqError("Groq returned an incomplete book draft.")
    return {"title": title[:200], "text_content": text_content[:30000]}


def score_pronunciation(expected_sentence, spoken_transcript, config, model=None):
    """Ask Groq to score transcript fidelity, returning (score, feedback)."""
    prompt = f"""
You are scoring a child's pronunciation reading from an ASR transcript.

Target sentence: {expected_sentence}
Spoken transcript: {spoken_transcript}

Score how faithfully the spoken transcript matches the target sentence.
Use 100 for an exact or near-exact reading, lower scores for missing,
substituted, or extra words. Do not reward a paraphrase as a correct reading.
Return ONLY valid JSON with exactly these fields:
{{"accuracy": 0, "feedback": "short encouraging feedback"}}
The accuracy must be an integer from 0 to 100. Keep feedback under 160 characters.
"""
    content = _chat_completion(
        [
            {
                "role": "system",
                "content": "You grade transcript fidelity consistently and return only the requested JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        model,
        config,
        temperature=0.1,
        max_tokens=180,
    )
    try:
        result = _json_from_content(content)
        accuracy = int(result["accuracy"])
        feedback = str(result.get("feedback") or "Keep reading clearly and try again.").strip()
    except (KeyError, IndexError, TypeError, ValueError) as err:
        raise GroqError("Groq returned an invalid pronunciation score.") from err

    return max(0, min(100, accuracy)), feedback[:160]
