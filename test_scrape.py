import tempfile
import unittest
from pathlib import Path

from scrape import parse_word_list, validate_word_list, write_word_list


SAMPLE_HTML = """
<table>
  <tr><th>Date</th><th>Wordle #</th><th>Answer</th></tr>
  <tr>
    <td>Today<br>Sep. 01</td>
    <td>1</td>
    <td><button>Reveal</button><span style="display:none">REBUT</span></td>
  </tr>
  <tr><td>Jun. 19</td><td>0</td><td>CIGAR</td></tr>
</table>
"""


class ScraperTests(unittest.TestCase):
    def test_parses_hidden_current_answer_as_a_clean_word(self):
        self.assertEqual(
            parse_word_list(SAMPLE_HTML),
            [
                {
                    "date": "Today Sep. 01",
                    "puzzle_number": "1",
                    "word": "rebut",
                },
                {
                    "date": "Jun. 19",
                    "puzzle_number": "0",
                    "word": "cigar",
                },
            ],
        )

    def test_rejects_empty_results(self):
        with self.assertRaisesRegex(ValueError, "only 0 entries"):
            validate_word_list([], [], min_entries=1)

    def test_rejects_non_contiguous_history(self):
        entries = [
            {"date": "Jun. 21", "puzzle_number": "2", "word": "sissy"},
            {"date": "Jun. 19", "puzzle_number": "0", "word": "cigar"},
        ]

        with self.assertRaisesRegex(ValueError, "not contiguous"):
            validate_word_list(entries, [], min_entries=1)

    def test_rejects_history_shrinkage(self):
        current = [
            {"date": "Jun. 19", "puzzle_number": "0", "word": "cigar"}
        ]
        previous = [
            {"date": "Jun. 20", "puzzle_number": "1", "word": "rebut"},
            {"date": "Jun. 19", "puzzle_number": "0", "word": "cigar"},
        ]

        with self.assertRaisesRegex(ValueError, "shrink history"):
            validate_word_list(current, previous, min_entries=1)

    def test_writes_valid_json_atomically(self):
        entries = parse_word_list(SAMPLE_HTML)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wordle_word_list.json"
            write_word_list(path, entries)
            self.assertIn('"word": "rebut"', path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
