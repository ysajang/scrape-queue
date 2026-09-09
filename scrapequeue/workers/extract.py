"""Row extraction for the three shapes a page can arrive in.

Selectors are written once in the target spec and used by all three, so a site
that changes its markup is a one-file change with a fixture to prove it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from scrapequeue.core.targets import TargetSpec

Row = dict[str, Any]


def _split_attr(expr: str) -> tuple[str, str]:
    selector, _, attr = expr.rpartition("@")
    if not selector:
        raise ValueError(f"attribute selector must be 'css@attr', got {expr!r}")
    return selector, attr


def columns(spec: TargetSpec) -> list[str]:
    return list(spec.extraction.fields) + list(spec.extraction.attributes)


def rows_from_html(html: str, spec: TargetSpec, page_url: str) -> list[Row]:
    from lxml import html as lxml_html

    tree: Any = lxml_html.fromstring(html)
    out: list[Row] = []
    for node in tree.cssselect(spec.extraction.row_selector):
        row: Row = {}
        for name, selector in spec.extraction.fields.items():
            found = node.cssselect(selector)
            row[name] = found[0].text_content().strip() if found else None
        for name, expr in spec.extraction.attributes.items():
            selector, attr = _split_attr(expr)
            found = node.cssselect(selector)
            value = found[0].get(attr) if found else None
            row[name] = urljoin(page_url, value) if value and attr == "href" else value
        out.append(row)
    return out


def rows_from_page(page: Any, spec: TargetSpec) -> list[Row]:
    """Extract from a live Playwright page.

    Done in one evaluate() call rather than a locator call per cell: a 25-row
    table with six columns is 150 round trips to the browser otherwise, which
    dominates the runtime of the whole page.
    """
    script = """
    ([rowSelector, fields, attrs]) => {
      const text = (el) => el ? el.innerText.trim() : null;
      return Array.from(document.querySelectorAll(rowSelector)).map((row) => {
        const out = {};
        for (const [name, sel] of Object.entries(fields)) {
          out[name] = text(row.querySelector(sel));
        }
        for (const [name, expr] of Object.entries(attrs)) {
          const at = expr.lastIndexOf('@');
          const sel = expr.slice(0, at), attr = expr.slice(at + 1);
          const el = row.querySelector(sel);
          out[name] = el ? el.getAttribute(attr) : null;
        }
        return out;
      });
    }
    """
    raw: list[Row] = page.evaluate(
        script,
        [spec.extraction.row_selector, spec.extraction.fields, spec.extraction.attributes],
    )
    for row in raw:
        for name, expr in spec.extraction.attributes.items():
            if expr.endswith("@href") and row.get(name):
                row[name] = urljoin(page.url, str(row[name]))
    return raw


def _dotted(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if not part.isdigit() or int(part) >= len(cur):
                return None
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def rows_from_json(payload: Any, spec: TargetSpec) -> list[Row]:
    container = _dotted(payload, spec.extraction.row_selector)
    if not isinstance(container, list):
        return []
    out: list[Row] = []
    for item in container:
        row: Row = {name: _dotted(item, path) for name, path in spec.extraction.fields.items()}
        out.append(row)
    return out


def row_identity(row: Row, spec: TargetSpec) -> str:
    keys = spec.extraction.row_key or sorted(row)
    return "|".join(str(row.get(k) or "") for k in keys)
