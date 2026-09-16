import argparse
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


SOURCE_URL = "https://www.nytimes.com/svc/wordle/v2/{date}.json"
OUTPUT_PATH = Path("wordle_word_list.json")
FIRST_PUZZLE_DATE = date(2021, 6, 19)
MIN_EXPECTED_ENTRIES = 1000
RETRY_DELAYS = (5, 10)
RETRY_STATUSES = {403, 408, 429, 500, 502, 503, 504}
REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Wordle-Checker-Collector/1.0 (+https://github.com/nmpraveen/wordle)",
}


def current_date():
    return datetime.now(ZoneInfo("America/New_York")).date()


def date_from_puzzle(puzzle):
    return FIRST_PUZZLE_DATE + timedelta(days=puzzle)


def normalize_entry(entry):
    if not isinstance(entry, dict):
        raise ValueError("History contains a non-object entry")

    puzzle = str(entry.get("puzzle_number", ""))
    word = entry.get("word")
    if not re.fullmatch(r"[0-9]+", puzzle):
        raise ValueError("History contains an invalid puzzle number")
    if not isinstance(word, str) or not re.fullmatch(r"[a-z]{5}", word):
        raise ValueError(f"History contains an invalid word for puzzle #{puzzle}")

    expected_date = date_from_puzzle(int(puzzle)).isoformat()
    stored_date = entry.get("date", "")
    if isinstance(stored_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", stored_date):
        if stored_date != expected_date:
            raise ValueError(f"History date does not match puzzle #{puzzle}")

    # Old WordFinder labels omit years or say "Today". Puzzle numbers provide
    # unambiguous calendar dates without changing any historical answer.
    return {"date": expected_date, "puzzle_number": str(int(puzzle)), "word": word}


def load_word_list(path):
    if not path.exists():
        raise ValueError("Existing history is missing; refusing to rebuild it implicitly")
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, list):
        raise ValueError(f"Existing {path} is not a JSON array")
    return [normalize_entry(entry) for entry in value]


def validate_word_list(word_list, previous_word_list, min_entries=MIN_EXPECTED_ENTRIES):
    if not word_list or len(word_list) < min_entries:
        raise ValueError(
            f"Refusing to replace history with only {len(word_list)} entries "
            f"(minimum {min_entries})"
        )

    normalized = [normalize_entry(entry) for entry in word_list]
    puzzles = [int(entry["puzzle_number"]) for entry in normalized]
    if len(set(puzzles)) != len(puzzles):
        raise ValueError("History contains duplicate puzzle numbers")
    if sorted(puzzles) != list(range(len(word_list))):
        raise ValueError("History is not contiguous from puzzle 0")
    if previous_word_list and len(word_list) < len(previous_word_list):
        raise ValueError(
            f"Refusing to shrink history from {len(previous_word_list)} "
            f"to {len(word_list)} entries"
        )

    words = {entry["puzzle_number"]: entry["word"] for entry in normalized}
    for previous in previous_word_list:
        entry = normalize_entry(previous)
        if words.get(entry["puzzle_number"]) != entry["word"]:
            raise ValueError(f"Refusing to change historical puzzle #{entry['puzzle_number']}")


def parse_puzzle(payload, requested_date):
    if not isinstance(payload, dict):
        raise ValueError("NYT response is not a JSON object")
    if payload.get("print_date") != requested_date.isoformat():
        raise ValueError(f"NYT response date does not match {requested_date}")

    puzzle = payload.get("days_since_launch")
    expected_puzzle = (requested_date - FIRST_PUZZLE_DATE).days
    if type(puzzle) is not int or puzzle != expected_puzzle or puzzle < 0:
        raise ValueError(f"NYT puzzle number does not match {requested_date}")
    return normalize_entry({"puzzle_number": puzzle, "word": payload.get("solution")})


def fetch_puzzle(requested_date):
    url = SOURCE_URL.format(date=requested_date.isoformat())
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=(10, 30))
            response.raise_for_status()
        except requests.RequestException as error:
            status = error.response.status_code if error.response is not None else None
            transient = isinstance(error, (requests.ConnectionError, requests.Timeout))
            retryable = transient or status in RETRY_STATUSES
            print(
                f"NYT fetch failed for {requested_date}: "
                f"attempt {attempt}/{attempts}, status {status or 'network error'}",
                flush=True,
            )
            if not retryable or attempt == attempts:
                raise
            time.sleep(RETRY_DELAYS[attempt - 1])
            continue

        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            raise ValueError(f"NYT returned non-JSON content for {requested_date}")
        return parse_puzzle(response.json(), requested_date)


def collect_word_list(previous_word_list, today, min_entries=MIN_EXPECTED_ENTRIES):
    validate_word_list(previous_word_list, [], min_entries=min_entries)
    latest_puzzle = max(int(entry["puzzle_number"]) for entry in previous_word_list)
    current_puzzle = (today - FIRST_PUZZLE_DATE).days
    if latest_puzzle > current_puzzle:
        raise ValueError("History contains a future puzzle; refusing to publish")

    collected = []
    for puzzle in range(latest_puzzle + 1, current_puzzle + 1):
        puzzle_date = date_from_puzzle(puzzle)
        entry = fetch_puzzle(puzzle_date)
        collected.append(entry)
        print(f"Collected NYT puzzle #{puzzle} for {puzzle_date} (answer withheld).")

    combined = previous_word_list + collected
    validate_word_list(combined, previous_word_list, min_entries=min_entries)
    return sorted(combined, key=lambda entry: int(entry["puzzle_number"]), reverse=True)


def write_word_list(path, word_list):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(word_list, temporary, indent=4)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def run_collector(path, today, min_entries=MIN_EXPECTED_ENTRIES):
    previous_word_list = load_word_list(path)
    word_list = collect_word_list(previous_word_list, today, min_entries=min_entries)
    write_word_list(path, word_list)
    print(f"Collected and validated {len(word_list)} Wordle puzzles through {today}.")


def main():
    parser = argparse.ArgumentParser(description="Collect missing NYT daily Wordle puzzles")
    parser.add_argument(
        "--verify-source", action="store_true",
        help="Verify yesterday and today without modifying history",
    )
    args = parser.parse_args()
    today = current_date()
    if args.verify_source:
        for puzzle_date in (today - timedelta(days=1), today):
            entry = fetch_puzzle(puzzle_date)
            print(
                f"Verified NYT JSON for {puzzle_date}, puzzle #{entry['puzzle_number']} "
                "(answer withheld; no files changed)."
            )
        return
    run_collector(OUTPUT_PATH, today)


if __name__ == "__main__":
    main()
