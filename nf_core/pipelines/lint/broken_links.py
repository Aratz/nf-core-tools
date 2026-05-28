import logging
import re
import sqlite3

import requests

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]\}]+")
URL_TRAILING_PUNCTUATION = ".,;:'\")]}>"
REQUEST_TIMEOUT: float = 5


def broken_links(self):
    """Check that external links in markdown files are not broken (HTTP 404).

    This lint test scans every Markdown (``.md``) file tracked by the pipeline
    repository, extracts all ``http://`` / ``https://`` URLs from each line,
    and issues a **warning** for every occurrence of a URL that returns an
    HTTP ``404`` status code.

    The same dead URL appearing on multiple lines / in multiple files produces
    one warning per occurrence, each citing the source file and line number.
    Internally, the network is only contacted once per unique URL.

    All other HTTP outcomes (``200``, ``301``, ``403``, ``5xx``, ...) and all
    network errors (timeouts, DNS failures, TLS errors, ...) are passed
    silently — only confirmed ``404`` responses are reported.

    .. tip:: You can choose to ignore this lint test by editing the file called
        ``.nf-core.yml`` in the root of your pipeline and setting the test to false:

        .. code-block:: yaml

            lint:
                broken_links: False

        To ignore individual URLs or markdown files, you can pass a list of
        URL prefixes and/or file paths. URLs starting with any of the listed
        prefixes are skipped, and markdown files matching any of the listed
        relative paths are not scanned at all:

        .. code-block:: yaml

            lint:
                broken_links:
                    - https://example.com/known-flaky
                    - docs/external_references.md
    """
    passed: list[str] = []
    warned: list[str] = []
    ignored: list[str] = []

    cfg = self.lint_config.get("broken_links", None) if self.lint_config is not None else None
    ignore_entries = cfg if isinstance(cfg, list) else []

    md_files = [fn for fn in self.list_files() if str(fn).lower().endswith(".md")]

    occurrences: list[tuple[str, int, str]] = []
    for md in md_files:
        rel = str(md.relative_to(self.wf_path))
        if rel in ignore_entries:
            ignored.append(f"Ignoring markdown file `{rel}`")
            continue
        try:
            with open(md, encoding="latin1") as fh:
                for lineno, line in enumerate(fh, start=1):
                    for raw in URL_RE.findall(line):
                        url = raw.rstrip(URL_TRAILING_PUNCTUATION)
                        occurrences.append((rel, lineno, url))
        except FileNotFoundError:
            log.debug(f"Could not open file {md} in broken_links lint test")

    status_cache: dict[str, bool] = {}
    for rel, lineno, url in occurrences:
        if any(url.startswith(prefix) for prefix in ignore_entries):
            ignored.append(f"Ignoring URL `{url}` at `{rel}:{lineno}`")
            continue
        if url not in status_cache:
            status_cache[url] = _is_404(url)
        if status_cache[url]:
            warned.append(f"Broken link (404): `{url}` at `{rel}:{lineno}`")

    if not warned:
        passed.append(f"No broken (404) links found in markdown files ({len(md_files)} files scanned)")

    return {"passed": passed, "failed": [], "warned": warned, "ignored": ignored}


def _is_404(url: str) -> bool:
    """Return True iff a HEAD request to ``url`` returns HTTP 404.

    Any other status code or any network-layer error returns False, so that
    only confirmed 404 responses trigger a warning in the calling lint test.
    """
    try:
        response = requests.head(url, stream=True, allow_redirects=True, timeout=REQUEST_TIMEOUT)
    except (requests.exceptions.RequestException, sqlite3.InterfaceError) as e:
        log.debug(f"Unable to connect to url '{url}' due to error: {e}")
        return False
    return response.status_code == 404
