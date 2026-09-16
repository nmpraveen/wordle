import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from scrape import (
    collect_word_list, current_date, fetch_puzzle, load_word_list, main,
    normalize_entry, parse_puzzle, run_collector, validate_word_list, write_word_list,
)


DAY_ZERO = date(2021, 6, 19)


def entry(puzzle, word="cigar"):
    return {
        "date": date.fromordinal(DAY_ZERO.toordinal() + puzzle).isoformat(),
        "puzzle_number": str(puzzle),
        "word": word,
    }


def response(status=200, payload=None, content_type="application/json"):
    result = requests.Response()
    result.status_code = status
    result.url = "https://www.nytimes.com/svc/wordle/v2/2021-06-19.json"
    result.headers["content-type"] = content_type
    result._content = json.dumps(payload if payload is not None else {
        "solution": "cigar", "print_date": "2021-06-19", "days_since_launch": 0,
    }).encode()
    return result


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_parses_valid_nyt_puzzle(self):
        self.assertEqual(parse_puzzle(response().json(), DAY_ZERO), entry(0))

    def test_rejects_bad_nyt_payloads(self):
        valid = response().json()
        for payload in (
            [], {}, {**valid, "print_date": "2021-06-20"},
            {**valid, "days_since_launch": 1},
            {**valid, "days_since_launch": False},
            {**valid, "days_since_launch": "0"},
            {**valid, "solution": "CIGAR"},
            {**valid, "solution": "cigar extra"},
            {**valid, "solution": None},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_puzzle(payload, DAY_ZERO)

    def test_legacy_dates_normalize_without_changing_answers(self):
        self.assertEqual(
            normalize_entry({"date": "Today Jun. 19", "puzzle_number": "0", "word": "cigar"}),
            entry(0),
        )

    def test_rejects_wrong_explicit_history_date(self):
        with self.assertRaisesRegex(ValueError, "date does not match"):
            normalize_entry({**entry(0), "date": "2021-06-20"})

    def test_rejects_malformed_history_entries(self):
        for value in ([], None, {}, {**entry(0), "word": 5}, {**entry(0), "puzzle_number": "-1"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_entry(value)

    def test_rejects_empty_results(self):
        with self.assertRaisesRegex(ValueError, "only 0 entries"):
            validate_word_list([], [], min_entries=1)

    def test_rejects_small_archive(self):
        with self.assertRaisesRegex(ValueError, "minimum 1000"):
            validate_word_list([entry(0)], [])

    def test_rejects_non_contiguous_history(self):
        with self.assertRaisesRegex(ValueError, "not contiguous"):
            validate_word_list([entry(0), entry(2)], [], min_entries=1)

    def test_rejects_duplicate_puzzles(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_word_list([entry(0), entry(0)], [], min_entries=1)

    def test_rejects_history_shrinkage(self):
        with self.assertRaisesRegex(ValueError, "shrink history"):
            validate_word_list([entry(0)], [entry(0), entry(1)], min_entries=1)

    def test_rejects_changed_historical_answer(self):
        with self.assertRaisesRegex(ValueError, "change historical puzzle"):
            validate_word_list([entry(0, "rebut")], [entry(0)], min_entries=1)

    def test_allows_same_answer_on_different_days(self):
        validate_word_list([entry(0), entry(1)], [entry(0)], min_entries=1)

    @patch("scrape.fetch_puzzle")
    def test_fetches_only_missing_days_and_catches_up(self, fetch):
        fetch.side_effect = [entry(1, "rebut"), entry(2, "sissy")]
        result = collect_word_list([entry(0)], date(2021, 6, 21), min_entries=1)
        self.assertEqual(result, [entry(2, "sissy"), entry(1, "rebut"), entry(0)])
        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            [date(2021, 6, 20), date(2021, 6, 21)],
        )

    @patch("scrape.fetch_puzzle")
    def test_current_archive_needs_no_requests(self, fetch):
        self.assertEqual(collect_word_list([entry(0)], DAY_ZERO, min_entries=1), [entry(0)])
        fetch.assert_not_called()

    @patch("scrape.fetch_puzzle")
    def test_invalid_archive_is_rejected_before_network(self, fetch):
        with self.assertRaises(ValueError):
            collect_word_list([entry(1)], date(2021, 6, 20), min_entries=1)
        fetch.assert_not_called()

    def test_future_history_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "future puzzle"):
            collect_word_list([entry(0), entry(1)], DAY_ZERO, min_entries=1)

    @patch("scrape.time.sleep")
    @patch("scrape.requests.get")
    def test_retries_temporary_http_errors(self, get, sleep):
        for status in (403, 429, 500, 503):
            with self.subTest(status=status):
                get.reset_mock()
                sleep.reset_mock()
                get.side_effect = [response(status), response()]
                self.assertEqual(fetch_puzzle(DAY_ZERO), entry(0))
                self.assertEqual(get.call_count, 2)
                sleep.assert_called_once_with(5)

    @patch("scrape.time.sleep")
    @patch("scrape.requests.get")
    def test_retries_network_errors_with_bounded_delays(self, get, sleep):
        get.side_effect = [requests.Timeout(), requests.ConnectionError(), response()]
        self.assertEqual(fetch_puzzle(DAY_ZERO), entry(0))
        self.assertEqual(get.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])

    @patch("scrape.time.sleep")
    @patch("scrape.requests.get")
    def test_stops_after_three_rejected_requests(self, get, sleep):
        get.return_value = response(403)
        with self.assertRaises(requests.HTTPError):
            fetch_puzzle(DAY_ZERO)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("scrape.time.sleep")
    @patch("scrape.requests.get")
    def test_does_not_retry_permanent_http_error(self, get, sleep):
        get.return_value = response(404)
        with self.assertRaises(requests.HTTPError):
            fetch_puzzle(DAY_ZERO)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    @patch("scrape.time.sleep")
    @patch("scrape.requests.get")
    def test_does_not_retry_invalid_puzzle_data(self, get, sleep):
        get.return_value = response(payload={"print_date": "2021-06-20"})
        with self.assertRaises(ValueError):
            fetch_puzzle(DAY_ZERO)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    @patch("scrape.requests.get")
    def test_rejects_non_json_and_malformed_json(self, get):
        get.return_value = response(content_type="text/html")
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            fetch_puzzle(DAY_ZERO)
        get.return_value = response()
        get.return_value._content = b"not json"
        with self.assertRaises(ValueError):
            fetch_puzzle(DAY_ZERO)

    def test_failed_catch_up_leaves_original_file_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wordle_word_list.json"
            write_word_list(path, [entry(0)])
            original = path.read_bytes()
            with patch("scrape.fetch_puzzle", side_effect=[entry(1), requests.Timeout()]):
                with self.assertRaises(requests.Timeout):
                    run_collector(path, date(2021, 6, 21), min_entries=1)
            self.assertEqual(path.read_bytes(), original)

    def test_writes_valid_json_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wordle_word_list.json"
            write_word_list(path, [entry(0)])
            self.assertEqual(load_word_list(path), [entry(0)])
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_atomic_write_preserves_file_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wordle_word_list.json"
            write_word_list(path, [entry(0)])
            original = path.read_bytes()
            with patch("scrape.os.replace", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    write_word_list(path, [entry(0), entry(1)])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_missing_archive_is_not_implicitly_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "history is missing"):
                load_word_list(Path(directory) / "missing.json")

    def test_verify_source_does_not_load_or_write_history(self):
        with (
            patch("sys.argv", ["scrape.py", "--verify-source"]),
            patch("scrape.current_date", return_value=date(2021, 6, 20)),
            patch("scrape.fetch_puzzle", side_effect=[entry(0), entry(1)]) as fetch,
            patch("scrape.load_word_list") as load,
            patch("scrape.write_word_list") as write,
        ):
            main()
        self.assertEqual(fetch.call_count, 2)
        load.assert_not_called()
        write.assert_not_called()

    def test_calendar_date_uses_new_york_not_utc(self):
        instant = datetime(2026, 9, 16, 2, tzinfo=timezone.utc)
        with patch("scrape.datetime") as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            self.assertEqual(current_date(), date(2026, 9, 15))


if __name__ == "__main__":
    unittest.main()
