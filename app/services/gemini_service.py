"""Gemini-powered generation for book-linked learning activities."""
import json
import re

import requests


class GeminiError(Exception):
    """A Gemini setup, API, or structured-output error."""


API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


QUESTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "word": {"type": "STRING"},
                    "question": {"type": "STRING"},
                    "options": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "answer": {"type": "STRING"},
                    "hint": {"type": "STRING"},
                    "explanation": {"type": "STRING"},
                },
                "required": ["word", "question", "options", "answer", "hint", "explanation"],
            },
        }
    },
    "required": ["questions"],
}


BOOK_DRAFT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "text_content": {"type": "STRING"},
    },
    "required": ["title", "text_content"],
}


def _api_key(config):
    key = str(config.get("GEMINI_API_KEY") or config.get("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise GeminiError("Gemini is not configured. Set GEMINI_API_KEY on the API server.")
    return key


def _error_message(response):
    try:
        payload = response.json()
        return str(payload.get("error", {}).get("message") or payload.get("message") or response.reason)
    except (ValueError, AttributeError):
        return f"Gemini returned HTTP {response.status_code}."


def _normalise_question(raw, book_text):
    if not isinstance(raw, dict):
        return None
    word = str(raw.get("word") or "").strip()
    question = str(raw.get("question") or "").strip()
    answer = str(raw.get("answer") or "").strip()
    options = raw.get("options")
    if not word or not question or not answer or not isinstance(options, list):
        return None

    options = [str(option).strip() for option in options if str(option).strip()]
    unique_options = []
    seen_options = set()
    for option in options:
        if option.casefold() not in seen_options:
            unique_options.append(option)
            seen_options.add(option.casefold())
    options = unique_options
    matching_answer = next((option for option in options if option.casefold() == answer.casefold()), None)
    if len(options) != 4 or matching_answer is None:
        return None
    # Keep generated questions grounded in the actual book. Case-insensitive
    # matching lets Gemini return normal title casing for a story word.
    word_pattern = rf"(?<!\w){re.escape(word)}(?!\w)"
    if not re.search(word_pattern, book_text, flags=re.IGNORECASE):
        return None
    return {
        "word": word,
        "question": question[:240],
        "options": [option[:80] for option in options],
        "answer": matching_answer[:80],
        "hint": str(raw.get("hint") or "Look back at how this word was used in the story.").strip()[:180],
        "explanation": str(raw.get("explanation") or "This answer matches the way the word was used in the story.").strip()[:240],
    }


def generate_story_word_quiz(book, config):
    """Generate eight child-friendly, book-grounded multiple-choice questions."""
    text = (book.text_content or "").strip()
    if not text:
        raise GeminiError("This book has no text available for quiz generation.")

    model = str(config.get("GEMINI_MODEL", "gemini-2.5-flash")).strip()
    prompt = f"""
Create a fun story-word quiz for a child reading this book.

Book title: {book.title}
Age group: {book.age_group}
Reading level: {book.reading_level}
Book text:
---
{text[:30000]}
---

Return exactly 8 questions, or at least 6 questions if the story is too short for 8.
Every question must test a word or phrase that appears exactly in the book text.
Mix these question styles:
1) meaning in the story,
2) choosing the word that completes a story idea,
3) remembering how a word was used in context.
Do not ask generic questions such as “Which word was in the book?”
Make each question short, warm, and understandable for the stated age group.
Each question must have exactly four short options and exactly one correct answer.
The answer must be one of the four options. Make distractors plausible but clearly wrong when the story is understood.
Add a useful hint that does not reveal the answer and a one-sentence explanation for after the child answers.
Do not invent events, characters, facts, or vocabulary that are not supported by the book.
"""
    try:
        response = requests.post(
            f"{API_BASE_URL}/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": _api_key(config)},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": QUESTION_SCHEMA,
                    "temperature": 0.55,
                    "maxOutputTokens": 2600,
                },
            },
            timeout=(10, max(1, int(config.get("GEMINI_REQUEST_TIMEOUT", 45)))),
        )
    except requests.RequestException as exc:
        raise GeminiError("Gemini could not be reached while creating this quiz.") from exc
    if not response.ok:
        raise GeminiError(f"Gemini could not create this quiz: {_error_message(response)}")

    try:
        payload = response.json()
        parts = payload["candidates"][0]["content"]["parts"]
        generated_text = "".join(str(part.get("text") or "") for part in parts)
        generated = json.loads(generated_text)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeminiError("Gemini returned quiz content in an unexpected format.") from exc

    questions = []
    seen_words = set()
    for raw in generated.get("questions", []) if isinstance(generated, dict) else []:
        question = _normalise_question(raw, text)
        if question and question["word"].casefold() not in seen_words:
            questions.append(question)
            seen_words.add(question["word"].casefold())
    if len(questions) < 6:
        raise GeminiError("Gemini did not return enough grounded quiz questions.")
    return questions[:8]


def generate_book_draft(age_group, reading_level, idea, config):
    """Generate a reviewed-ready story draft without exposing Gemini to the browser."""
    idea = str(idea or "").strip()
    if not idea:
        raise GeminiError("A story idea is required.")
    prompt = f"""
Write an original children's story for the TeachAlike reading app.

Age group: {str(age_group or '').strip()}
Reading level: {str(reading_level or '').strip()}
Story idea: {idea[:500]}

Return a short, engaging story with a clear beginning, middle, and ending.
Use warm, age-appropriate language, vivid but safe imagery, and short paragraphs.
Do not include a preface, lesson-plan notes, markdown headings, or questions.
Return only the story title and story text in the requested JSON format.
"""
    model = str(config.get("GEMINI_MODEL", "gemini-2.5-flash")).strip()
    try:
        response = requests.post(
            f"{API_BASE_URL}/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": _api_key(config)},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": BOOK_DRAFT_SCHEMA,
                    "temperature": 0.8,
                    "maxOutputTokens": 1800,
                },
            },
            timeout=(10, max(1, int(config.get("GEMINI_REQUEST_TIMEOUT", 45)))),
        )
    except requests.RequestException as exc:
        raise GeminiError("Gemini could not be reached while creating this book draft.") from exc
    if not response.ok:
        raise GeminiError(f"Gemini could not create this book draft: {_error_message(response)}")

    try:
        payload = response.json()
        parts = payload["candidates"][0]["content"]["parts"]
        generated_text = "".join(str(part.get("text") or "") for part in parts)
        generated = json.loads(generated_text)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeminiError("Gemini returned book content in an unexpected format.") from exc

    title = str(generated.get("title") or "").strip() if isinstance(generated, dict) else ""
    text_content = str(generated.get("text_content") or "").strip() if isinstance(generated, dict) else ""
    if not title or not text_content:
        raise GeminiError("Gemini returned an incomplete book draft.")
    return {"title": title[:200], "text_content": text_content[:30000]}
