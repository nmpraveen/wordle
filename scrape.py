import json
import os
import re
import tempfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup


SOURCE_URL = "https://wordfinder.yourdictionary.com/wordle/answers/"
OUTPUT_PATH = Path("wordle_word_list.json")
MIN_EXPECTED_ENTRIES = 1000
WORD_PATTERN = re.compile(r"(?<![a-z])[a-z]{5}(?![a-z])")
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/140.0 Safari/537.36"
    )
}


def extract_word(value):
    matches = WORD_PATTERN.findall(value.lower())
    return matches[-1] if matches else ""


def parse_word_list(html):
    soup = BeautifulSoup(html, "html.parser")
    word_list = []

    for row in soup.select("table tr"):
        columns = row.find_all("td")
        if len(columns) != 3:
            continue

        date = columns[0].get_text(" ", strip=True)
        puzzle_number = columns[1].get_text(" ", strip=True)
        word = extract_word(columns[2].get_text(" ", strip=True))
        if not date or not puzzle_number.isdigit() or not word:
            continue

        word_list.append(
            {
                "date": date,
                "puzzle_number": puzzle_number,
                "word": word,
            }
        )

    return word_list


def load_word_list(path):
    if not path.exists():
        return []

    with path.open(encoding="utf-8") as source:
        value = json.load(source)

    if not isinstance(value, list):
        raise ValueError(f"Existing {path} is not a JSON array")

    return value


def validate_word_list(
    word_list, previous_word_list, min_entries=MIN_EXPECTED_ENTRIES
):
    if len(word_list) < min_entries:
        raise ValueError(
            f"Refusing to replace history with only {len(word_list)} entries "
            f"(minimum {min_entries})"
        )

    puzzles = []
    for entry in word_list:
        puzzle_number = str(entry.get("puzzle_number", ""))
        word = entry.get("word", "")
        if not puzzle_number.isdigit() or not re.fullmatch(r"[a-z]{5}", word):
            raise ValueError(f"Invalid scraped entry: {entry!r}")
        puzzles.append(int(puzzle_number))

    if len(set(puzzles)) != len(puzzles):
        raise ValueError("Scraped history contains duplicate puzzle numbers")

    latest_puzzle = max(puzzles)
    expected_puzzles = set(range(latest_puzzle + 1))
    if set(puzzles) != expected_puzzles:
        raise ValueError("Scraped history is not contiguous from puzzle 0")

    if previous_word_list and len(word_list) < len(previous_word_list):
        raise ValueError(
            f"Refusing to shrink history from {len(previous_word_list)} "
            f"to {len(word_list)} entries"
        )


def write_word_list(path, word_list):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary:
            json.dump(word_list, temporary, indent=4)
            temporary.write("\n")
            temporary_path = Path(temporary.name)

        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def fetch_html():
    response = requests.get(
        SOURCE_URL,
        headers=REQUEST_HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        raise ValueError(f"Unexpected content type: {content_type or 'missing'}")

    return response.text


def main():
    previous_word_list = load_word_list(OUTPUT_PATH)
    word_list = parse_word_list(fetch_html())
    validate_word_list(word_list, previous_word_list)
    write_word_list(OUTPUT_PATH, word_list)
    print(f"Scraped and validated {len(word_list)} Wordle words successfully.")


if __name__ == "__main__":
    main()
