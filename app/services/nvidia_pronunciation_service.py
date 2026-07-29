"""NVIDIA scoring for a spoken transcript against the target sentence."""

import json
import re

import requests


class NvidiaPronunciationError(Exception):
    """A safe, user-facing NVIDIA pronunciation scoring error."""


def _api_key(config):
    key = str(
        config.get("NVIDIA_PRONUNCIATION_API_KEY")
        or config.get("NVIDIA_ASR_API_KEY")
        or config.get("NVIDIA_API_KEY")
        or config.get("NVAPI_KEY")
        or ""
    ).strip()
    if not key:
        raise NvidiaPronunciationError("NVIDIA pronunciation scoring is not configured.")
    return key


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


def score_pronunciation(expected_sentence, spoken_transcript, config, model=None):
    """Ask NVIDIA to score transcript fidelity, returning (score, feedback)."""
    url = str(config.get("NVIDIA_API_URL") or "").strip()
    model = str(model or config.get("NVIDIA_MODEL") or "openai/gpt-oss-120b").strip()
    if not url:
        raise NvidiaPronunciationError("NVIDIA pronunciation scoring URL is not configured.")

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
    timeout = max(10, int(config.get("NVIDIA_PRONUNCIATION_REQUEST_TIMEOUT", 20)))
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {_api_key(config)}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You grade transcript fidelity consistently and return only the requested JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 180,
                "stream": False,
            },
            timeout=(10, timeout),
        )
    except requests.RequestException as err:
        raise NvidiaPronunciationError("NVIDIA could not be reached for pronunciation scoring.") from err

    if not response.ok:
        raise NvidiaPronunciationError(
            f"NVIDIA pronunciation scoring failed with HTTP {response.status_code}."
        )
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        result = _json_from_content(content)
        accuracy = int(result["accuracy"])
        feedback = str(result.get("feedback") or "Keep reading clearly and try again.").strip()
    except (KeyError, IndexError, TypeError, ValueError) as err:
        raise NvidiaPronunciationError("NVIDIA returned an invalid pronunciation score.") from err

    return max(0, min(100, accuracy)), feedback[:160]
