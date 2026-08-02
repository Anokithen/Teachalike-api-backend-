"""Deterministic, Unicode-aware word alignment for reading practice."""

from __future__ import annotations

import re
import unicodedata


MAX_COMPARISON_CHARACTERS = 10_000
MAX_COMPARISON_WORDS = 500
_SENTENCE_END_RE = re.compile(r"[.!?。！？]+")


def _normalise_token(value: str) -> str:
    """Normalise only a comparison key, never the displayed/saved text."""
    return unicodedata.normalize("NFKC", value).replace("’", "'").casefold()


def _is_word_character(value: str) -> bool:
    return bool(value) and unicodedata.category(value)[0] in {"L", "M", "N"}


def _word_spans(text: str):
    """Yield Unicode letter/mark/number words with internal apostrophes."""
    start = None
    for index, character in enumerate(text):
        if _is_word_character(character):
            if start is None:
                start = index
            continue
        if (
            character in {"'", "’"}
            and start is not None
            and index + 1 < len(text)
            and _is_word_character(text[index + 1])
        ):
            continue
        if start is not None:
            yield start, index
            start = None
    if start is not None:
        yield start, len(text)


def _tokens(text: str, *, paragraph_index: int, locations: bool) -> list[dict]:
    if len(text) > MAX_COMPARISON_CHARACTERS:
        raise ValueError("Text is too long to compare safely.")

    result: list[dict] = []
    sentence_index = 0
    word_index = 0
    scan_position = 0
    for start, end in _word_spans(text):
        between = text[scan_position:start]
        sentence_breaks = len(_SENTENCE_END_RE.findall(between))
        if sentence_breaks:
            sentence_index += sentence_breaks
            word_index = 0
        item = {
            "display": text[start:end],
            "key": _normalise_token(text[start:end]),
        }
        if locations:
            item.update({
                "paragraph_index": paragraph_index,
                "sentence_index": sentence_index,
                "word_index": word_index,
                "global_word_index": len(result),
                "character_start": start,
                "character_end": end,
            })
        result.append(item)
        word_index += 1
        scan_position = end
        if len(result) > MAX_COMPARISON_WORDS:
            raise ValueError("Text contains too many words to compare safely.")
    return result


def _edit_operations(expected: list[dict], spoken: list[dict]) -> list[tuple[str, int | None, int | None]]:
    """Return a stable minimum-cost alignment using word Levenshtein distance."""
    rows, columns = len(expected) + 1, len(spoken) + 1
    costs = [[0] * columns for _ in range(rows)]
    moves: list[list[str | None]] = [[None] * columns for _ in range(rows)]
    for i in range(1, rows):
        costs[i][0], moves[i][0] = i, "deletion"
    for j in range(1, columns):
        costs[0][j], moves[0][j] = j, "insertion"

    for i in range(1, rows):
        for j in range(1, columns):
            equal = expected[i - 1]["key"] == spoken[j - 1]["key"]
            diagonal_status = "correct" if equal else "substitution"
            candidates = (
                (costs[i - 1][j - 1] + (0 if equal else 1), 0, diagonal_status),
                (costs[i - 1][j] + 1, 1, "deletion"),
                (costs[i][j - 1] + 1, 2, "insertion"),
            )
            cost, _, move = min(candidates)
            costs[i][j], moves[i][j] = cost, move

    operations: list[tuple[str, int | None, int | None]] = []
    i, j = len(expected), len(spoken)
    while i or j:
        move = moves[i][j]
        if move in {"correct", "substitution"}:
            operations.append((move, i - 1, j - 1))
            i -= 1
            j -= 1
        elif move == "deletion":
            operations.append((move, i - 1, None))
            i -= 1
        else:
            operations.append(("insertion", None, j - 1))
            j -= 1
    operations.reverse()
    return operations


def compare_pronunciation(original_text: str, spoken_text: str, paragraph_index: int) -> dict:
    """Align expected and spoken words and return API-ready comparison data."""
    if not isinstance(original_text, str) or not isinstance(spoken_text, str):
        raise ValueError("Original and spoken text must be strings.")
    expected = _tokens(original_text, paragraph_index=paragraph_index, locations=True)
    spoken = _tokens(spoken_text, paragraph_index=paragraph_index, locations=False)
    if not expected:
        raise ValueError("The selected paragraph has no words to compare.")
    if not spoken:
        raise ValueError("A spoken transcript is required.")

    operations = _edit_operations(expected, spoken)
    tokens: list[dict] = []
    practice_words: list[dict] = []
    previous_expected: int | None = None
    next_expected_by_operation: list[int | None] = [None] * len(operations)
    upcoming: int | None = None
    for operation_index in range(len(operations) - 1, -1, -1):
        expected_index = operations[operation_index][1]
        if expected_index is not None:
            upcoming = expected_index
        next_expected_by_operation[operation_index] = upcoming

    counts = {"correct": 0, "substitution": 0, "deletion": 0, "insertion": 0}
    for operation_index, (status, expected_index, spoken_index) in enumerate(operations):
        counts[status] += 1
        expected_token = expected[expected_index] if expected_index is not None else None
        spoken_token = spoken[spoken_index] if spoken_index is not None else None
        if expected_token is not None:
            token = {
                "status": status,
                "expected": expected_token["display"],
                "heard": spoken_token["display"] if spoken_token else None,
                **{key: expected_token[key] for key in (
                    "paragraph_index", "sentence_index", "word_index",
                    "global_word_index", "character_start", "character_end",
                )},
                "after_word_index": None,
                "before_word_index": None,
            }
            previous_expected = expected_index
            if status in {"substitution", "deletion"}:
                practice_words.append({
                    "expected": expected_token["display"],
                    "heard": spoken_token["display"] if spoken_token else None,
                    "status": status,
                    "sentence_number": expected_token["sentence_index"] + 1,
                    "word_number": expected_token["word_index"] + 1,
                    "global_word_index": expected_token["global_word_index"],
                })
        else:
            next_expected = next_expected_by_operation[operation_index]
            anchor = previous_expected if previous_expected is not None else next_expected
            anchor_token = expected[anchor] if anchor is not None else None
            token = {
                "status": "insertion",
                "expected": None,
                "heard": spoken_token["display"] if spoken_token else None,
                "paragraph_index": paragraph_index,
                "sentence_index": anchor_token["sentence_index"] if anchor_token else None,
                "word_index": None,
                "global_word_index": None,
                "character_start": None,
                "character_end": None,
                "after_word_index": previous_expected,
                "before_word_index": next_expected,
            }
        tokens.append(token)

    correct_words = counts["correct"]
    text_match_accuracy = round(correct_words / len(expected) * 100)
    return {
        "original_text": original_text,
        "spoken_text": spoken_text,
        "summary": {
            "expected_words": len(expected),
            "spoken_words": len(spoken),
            "correct_words": correct_words,
            "substitutions": counts["substitution"],
            "skipped_words": counts["deletion"],
            "extra_words": counts["insertion"],
            "words_needing_practice": counts["substitution"] + counts["deletion"],
        },
        "tokens": tokens,
        "practice_words": practice_words,
        "text_match_accuracy": text_match_accuracy,
    }
