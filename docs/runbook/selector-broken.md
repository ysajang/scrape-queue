# Runbook: a target's selectors stopped matching

## Symptoms

- Jobs finishing in state `SUSPECT` with an error like
  `possible selector drift: 2.0 rows/page against a baseline of 25`
- `sq_drift_suspects_total` incrementing
- Results present but far smaller than usual

## Why the job did not simply fail

Every task succeeded. The site returned 200, the parser ran, and it found
nothing. Without the drift check this would ship an empty CSV labelled
`SUCCEEDED`, which is the worst outcome available: silently wrong data.

## Fix

1. **Confirm it against the fixture** first. If the unit tests pass, the
   selectors still match the captured snapshot and the problem is elsewhere
   (an A/B test, a geo-specific layout, a partial outage):
   ```bash
   pytest tests/unit/test_extract.py
   ```

2. **Capture what the site returns now.** For a browser target:
   ```bash
   python - <<'PY'
   from playwright.sync_api import sync_playwright
   with sync_playwright() as p:
       b = p.chromium.launch(); pg = b.new_context().new_page()
       pg.goto("https://simpler.grants.gov/search?page=1", wait_until="domcontentloaded")
       pg.wait_for_selector("table tbody tr", state="attached")
       open("tests/fixtures/html/grants_browser_page1.html", "w").write(
           pg.evaluate("() => document.querySelector('table').outerHTML"))
       b.close()
   PY
   ```

3. **Update the selectors** in `targets/<name>.yml` and rerun the extraction
   tests until they pass against the new fixture. Commit the fixture with the
   spec change: the fixture is the evidence that the new selectors match a real
   page.

4. **Reset the baseline** if the row count legitimately changed (the site now
   shows 10 per page instead of 25), otherwise every future run stays `SUSPECT`:
   ```sql
   DELETE FROM drift_baselines WHERE target = 'grants_browser';
   ```
   The next successful run establishes a new baseline.

5. **Re-drive** the affected jobs with `POST /jobs/{id}/retry`.

## Prevention

CI runs weekly against the live targets, so drift is normally found by a failing
scheduled build rather than by a customer noticing an empty file.
