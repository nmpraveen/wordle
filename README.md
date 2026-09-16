# Wordle answer history

The existing wordle_word_list.json feed URL and fields (date, puzzle_number, word)
remain unchanged. Wordle Checker extensions need no changes for this collector.

## Collection

The collector requests missing days from NYT's daily puzzle JSON endpoint:
https://www.nytimes.com/svc/wordle/v2/YYYY-MM-DD.json.
This is an internal endpoint, not a guaranteed public API. It supplies daily
answers, not the complete candidate dictionary in officialanswers.js.

- The existing complete archive is required; it is never rebuilt implicitly.
- Historical answers are preserved. Legacy date labels normalize to ISO dates
  using calendar-day arithmetic from puzzle 0 on 2021-06-19.
- Only missing days through today's New York calendar date are requested.
- Responses must match the requested date, puzzle number, and [a-z]{5} answer.
- Temporary network/HTTP failures get at most three attempts with 5- and
  10-second delays. Invalid JSON or puzzle data fails immediately.
- No history is written until every missing day validates. Writes are atomic.
- Answers are not printed in logs.

GitHub Actions runs at 05:30 UTC and again at 11:30 UTC for catch-up. The second
run makes no NYT requests when the archive is already current. Publishing is
limited to scheduled/manual runs on main; pull requests only run offline tests.
History-only commits do not trigger another collection run.

## Tests and source verification

```sh
python -m pip install -r requirements.txt
python -m unittest discover -v
python scrape.py --verify-source
```

Verification checks yesterday and today without changing any file. In GitHub
Actions, manually run scrape-wordle.yml with verify_source=true to test the
endpoint from a hosted runner, including a feature branch before switching.

To collect missing days locally, run python scrape.py. If all attempts fail, the
existing archive stays untouched and GitHub reports a failed run. A later
successful run automatically collects every missed day.
