import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]\}]+")
URL_TRAILING_PUNCTUATION = ".,;:'\")]}>"
REQUEST_TIMEOUT: float = 5
# Upper bound on concurrent HEAD requests. Link checking is I/O-bound, so a
# small thread pool gives a large speed-up without hammering any single host.
MAX_WORKERS: int = 20


def broken_links(self):
    """Check that external links in markdown files are not broken (HTTP 404).

    This lint test scans the top-level ``README.md`` and every Markdown
    (``.md``) file under the ``docs/`` directory tracked by the pipeline
    repository, extracts all ``http://`` / ``https://`` URLs from each line,
    and issues a **warning** for every occurrence of a URL that returns an
    HTTP ``404`` status code.

    The same dead URL appearing on multiple lines / in multiple files produces
    one warning per occurrence, each citing the source file and line number.
    Internally, the network is only contacted once per unique URL.

    All other HTTP outcomes (``200``, ``301``, ``403``, ``5xx``, ...) and all
    network errors (timeouts, DNS failures, TLS errors, ...) are passed
    silently — only confirmed ``404`` responses are reported. Each request
    uses a 5-second timeout so an unresponsive server can never hang the
    linter. Unique URLs are checked concurrently over a shared
    :class:`requests.Session` (connection pooling) to keep the check fast even
    for pipelines with many links.

    Some URLs in a freshly templated nf-core pipeline are only expected to
    resolve **after** the pipeline's first release (for example
    ``https://nf-co.re/<short_name>/...``). On these unreleased pipelines, 404s
    on any markdown URL matching the ``nf-co.re/<short_name>/`` prefix are
    demoted from warnings to **ignored** entries with an explanatory message.

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

    md_files = [fn for fn in self.list_files() if _is_scanned_markdown(fn.relative_to(self.wf_path))]

    with requests.Session() as session:
        pre_release = _is_pre_release(self, session)
        post_release_prefixes = _post_release_prefixes(self) if pre_release else []

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

        def _needs_network_check(url: str) -> bool:
            return not any(
                url.startswith(prefix)
                for prefix in ignore_entries + post_release_prefixes
            )

        # Resolve every unique URL that needs checking once, concurrently.
        urls_to_check = sorted({url for _, _, url in occurrences if _needs_network_check(url)})
        status_cache: dict[str, bool] = {}
        if urls_to_check:
            log.debug(f"Checking {len(urls_to_check)} unique URLs for broken links")
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(urls_to_check))) as pool:
                status_cache = dict(
                        zip(
                            urls_to_check,
                            pool.map(lambda u: _is_404(u, session), urls_to_check),
                            strict=True,
                        ))

    # Report per occurrence so duplicate links each get their own message.
    for rel, lineno, url in occurrences:
        if any(url.startswith(prefix) for prefix in ignore_entries):
            ignored.append(f"Ignoring URL `{url}` at `{rel}:{lineno}`")
            continue
        if any(url.startswith(prefix) for prefix in post_release_prefixes):
            ignored.append(f"Pre-release URL not yet live (expected): `{url}` at `{rel}:{lineno}`")
            continue
        if status_cache.get(url, False):
            warned.append(f"Broken link (404): `{url}` at `{rel}:{lineno}`")

    if not warned:
        passed.append(f"No broken (404) links found in markdown files ({len(md_files)} files scanned)")

    return {"passed": passed, "failed": [], "warned": warned, "ignored": ignored}


def _is_scanned_markdown(rel_path) -> bool:
    """Return True for markdown files this check should scan.

    Limits the scan to the top-level ``README.md`` and any ``.md`` file under
    the ``docs/`` directory, rather than every markdown file in the repo.
    """
    parts = rel_path.parts
    return rel_path.suffix.lower() == ".md" and (parts[0] == "docs" or parts[0] == "README.md")


def _is_404(url: str, session: requests.Session | None = None) -> bool:
    """Return True iff a HEAD request to ``url`` returns HTTP 404.

    Any other status code or any network-layer error returns False, so that
    only confirmed 404 responses trigger a warning in the calling lint test.

    A shared :class:`requests.Session` can be passed to reuse connections
    across many requests; if omitted, the module-level ``requests`` is used.
    """
    requester = session if session is not None else requests
    try:
        response = requester.head(url, stream=True, allow_redirects=True, timeout=REQUEST_TIMEOUT)
    except (requests.exceptions.RequestException, sqlite3.InterfaceError) as e:
        log.debug(f"Unable to connect to url '{url}' due to error: {e}")
        return False
    return response.status_code == 404


def _is_pre_release(lint_obj, session: requests.Session | None = None) -> bool:
    """Heuristic for "this pipeline has not had its first release yet".

    Probes ``https://nf-co.re/<short_name>/`` with a single HEAD request and
    returns True only if that URL returns HTTP ``404`` — i.e. the pipeline's
    nf-co.re page does not exist yet, which we treat as "pre-release".

    Returns False in all other cases:

    * ``manifest.name`` is missing or does not look like ``<org>/<name>``,
    * the probe URL returned ``200``/``3xx`` (page already live),
    * the probe URL raised a network error / timeout (we cannot verify, so
      we err on the side of not silently hiding 404 warnings).

    When True, 404s on the matching nf-core URL prefix are demoted from
    ``warned`` to ``ignored`` in :func:`broken_links`.
    """
    nf_config = getattr(lint_obj, "nf_config", None) or {}
    name = (nf_config.get("manifest.name", "") or "").strip(" '\"")
    short = name.split("/", 1)[1] if "/" in name else ""
    if not short:
        return False
    probe_url = f"https://nf-co.re/{short}/"
    pre_release = _is_404(probe_url, session)
    log.debug(
        f"Pre-release probe `{probe_url}` -> {'pre-release (404)' if pre_release else 'released (or unreachable)'}"
    )
    return pre_release


def _post_release_prefixes(lint_obj) -> list[str]:
    """URL prefixes that only become live after the pipeline's first release.

    Currently this is just the pipeline's own page on ``nf-co.re``, which is
    generated when the pipeline is registered + first released.
    """
    nf_config = getattr(lint_obj, "nf_config", None) or {}
    name = (nf_config.get("manifest.name", "") or "").strip(" '\"")
    short = name.split("/", 1)[1] if "/" in name else ""
    return [f"https://nf-co.re/{short}/"] if short else []
