import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
import yaml

import nf_core.pipelines.lint

from ...utils import create_tmp_pipeline
from ..test_lint import TestLint


def _resp(status_code: int) -> MagicMock:
    """Build a fake ``requests.Response`` with the given status code."""
    response = MagicMock()
    response.status_code = status_code
    return response


def _selective_status(dead_urls: set[str]):
    """Return a ``requests.Session.head``/``get`` side-effect that returns 404
    for the given URLs and 200 for everything else.

    Test markdown files added by the tests use URLs under ``https://example.com/``
    or ``https://ignore.me/``. Any URLs already present in the nf-core template
    (e.g. in ``README.md``) are answered with 200 so they don't pollute results.
    """

    def _request(url, *args, **kwargs):
        return _resp(404 if url in dead_urls else 200)

    return _request


def _set_dead(mock_head, mock_get, dead_urls: set[str]) -> None:
    """Configure both the HEAD and GET mocks to 404 on ``dead_urls``.

    Both verbs share the same behaviour because ``_is_404`` confirms a HEAD 404
    with a GET before reporting a link as broken.
    """
    side = _selective_status(set(dead_urls))
    mock_head.side_effect = side
    mock_get.side_effect = side


class TestLintBrokenLinks(TestLint):
    """Broken-links lint tests.

    Creating the nf-core template pipeline is expensive (a full
    ``PipelineCreate().init_pipeline()`` with network access). Since every test
    only reads from the pristine pipeline and then operates on its own
    ``shutil.copytree`` copy, the template is built once for the whole class in
    ``setUpClass`` instead of once per test in ``setUp``.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._shared_tmp_dir, _, _, cls._shared_pipeline_dir = create_tmp_pipeline()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._shared_tmp_dir, ignore_errors=True)

    def setUp(self) -> None:
        # Give each test its own isolated copy of the shared template pipeline:
        # tests mutate files and ``.nf-core.yml`` in this copy.
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.pipeline_dir = self._shared_pipeline_dir
        self.new_pipeline = self._make_pipeline_copy()
        self.nf_core_yml_path = Path(self.new_pipeline) / ".nf-core.yml"
        with open(self.nf_core_yml_path) as f:
            self.nf_core_yml = yaml.safe_load(f)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_md(self, relpath: str, content: str) -> Path:
        path = Path(self.new_pipeline) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        # Stage file so that it is listed by `list_files`.
        subprocess.check_output(["git", "add", str(path)], cwd=self.new_pipeline)
        return path

    def _run_check(self):
        lint_obj = nf_core.pipelines.lint.PipelineLint(self.new_pipeline)
        lint_obj._load()
        return lint_obj.broken_links()

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_404_url_produces_one_warning(self, mock_head, mock_get):
        """A single 404 URL in a markdown file produces one warning citing file:line."""
        dead = "https://example.com/dead"
        _set_dead(mock_head, mock_get, {dead})
        self._write_md("docs/dead.md", f"Click [here]({dead}) please.\n")

        result = self._run_check()

        dead_warnings = [w for w in result["warned"] if dead in w]
        assert len(dead_warnings) == 1
        assert "docs/dead.md:1" in dead_warnings[0]
        assert result["failed"] == []

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_head_404_but_get_200_does_not_warn(self, mock_head, mock_get):
        """A HEAD 404 confirmed as 200 by GET is not reported (e.g. bsky.app)."""
        url = "https://bsky.app/profile/nf-co.re"
        # HEAD says 404, GET says 200 -> reachable -> no warning.
        mock_head.side_effect = _selective_status({url})
        mock_get.side_effect = _selective_status(set())
        self._write_md("docs/bsky.md", f"[bluesky]({url})\n")

        result = self._run_check()

        assert not any(url in w for w in result["warned"])
        # GET must have been used to confirm the HEAD 404.
        get_urls = [c.args[0] for c in mock_get.call_args_list if c.args]
        assert url in get_urls

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_200_url_does_not_warn(self, mock_head, mock_get):
        """A 200 URL produces no warning for that URL."""
        url = "https://example.com/ok"
        _set_dead(mock_head, mock_get, set())
        self._write_md("docs/ok.md", f"All good: {url}\n")

        result = self._run_check()

        assert not any(url in w for w in result["warned"])

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_network_error_does_not_warn(self, mock_head, mock_get):
        """Network exceptions are silently passed (strict-404 semantics)."""
        url = "https://example.com/unreachable"
        mock_head.side_effect = requests.exceptions.ConnectionError("boom")
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")
        self._write_md("docs/flaky.md", f"Maybe broken: {url}\n")

        result = self._run_check()

        assert result["warned"] == []

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_500_does_not_warn(self, mock_head, mock_get):
        """Non-404 error responses are silently passed."""
        url = "https://example.com/oops"

        def _request(u, *args, **kwargs):
            return _resp(500 if u == url else 200)

        mock_head.side_effect = _request
        mock_get.side_effect = _request
        self._write_md("docs/server.md", f"[server]({url})\n")

        result = self._run_check()

        assert not any(url in w for w in result["warned"])

    def test_disabled_via_nf_core_yml(self):
        """``broken_links: false`` in ``.nf-core.yml`` ignores the check at the orchestrator level."""
        valid_yaml = """
        broken_links: false
        """
        self.nf_core_yml["lint"] = yaml.safe_load(valid_yaml)
        with open(self.nf_core_yml_path, "w") as f:
            yaml.safe_dump(self.nf_core_yml, f)

        lint_obj = nf_core.pipelines.lint.PipelineLint(self.new_pipeline, key=["broken_links"])
        lint_obj._load()
        lint_obj._lint_pipeline()

        ignored_tests = [name for name, _ in lint_obj.ignored]
        assert "broken_links" in ignored_tests

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_ignore_url_prefix(self, mock_head, mock_get):
        """URLs matching an entry in ``lint.broken_links`` are reported as ignored, not warned, and are not fetched."""
        dead = "https://ignore.me/path"
        _set_dead(mock_head, mock_get, {dead})
        self._write_md("docs/ignored.md", f"[bad]({dead})\n")

        valid_yaml = """
        broken_links:
            - https://ignore.me
        """
        self.nf_core_yml["lint"] = yaml.safe_load(valid_yaml)
        with open(self.nf_core_yml_path, "w") as f:
            yaml.safe_dump(self.nf_core_yml, f)

        result = self._run_check()

        assert not any(dead in w for w in result["warned"])
        assert any(dead in m for m in result["ignored"])
        called_urls = [c.args[0] for c in mock_head.call_args_list if c.args]
        assert dead not in called_urls

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_ignore_markdown_file(self, mock_head, mock_get):
        """Markdown files listed in ``lint.broken_links`` are skipped entirely."""
        dead = "https://example.com/dead"
        _set_dead(mock_head, mock_get, {dead})
        self._write_md("docs/skipme.md", f"[bad]({dead})\n")

        valid_yaml = """
        broken_links:
            - docs/skipme.md
        """
        self.nf_core_yml["lint"] = yaml.safe_load(valid_yaml)
        with open(self.nf_core_yml_path, "w") as f:
            yaml.safe_dump(self.nf_core_yml, f)

        result = self._run_check()

        assert not any(dead in w for w in result["warned"])
        assert any("docs/skipme.md" in msg for msg in result["ignored"])
        called_urls = [c.args[0] for c in mock_head.call_args_list if c.args]
        assert dead not in called_urls

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_same_dead_url_twice_produces_two_warnings_one_check(self, mock_head, mock_get):
        """Duplicate dead URL across two files -> two warnings, but only one network check for that URL."""
        dead = "https://example.com/dead"
        _set_dead(mock_head, mock_get, {dead})
        self._write_md("docs/a.md", f"[bad]({dead})\n")
        self._write_md("docs/b.md", f"see [bad]({dead}) here\n")

        result = self._run_check()

        dead_warnings = [w for w in result["warned"] if dead in w]
        assert len(dead_warnings) == 2
        joined = " ".join(dead_warnings)
        assert "docs/a.md:1" in joined
        assert "docs/b.md:1" in joined
        # The URL is only resolved once (one HEAD + one confirming GET), despite two occurrences.
        head_calls = [c for c in mock_head.call_args_list if c.args and c.args[0] == dead]
        get_calls = [c for c in mock_get.call_args_list if c.args and c.args[0] == dead]
        assert len(head_calls) == 1
        assert len(get_calls) == 1

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_two_dead_urls_on_same_line(self, mock_head, mock_get):
        """Two URLs on the same line each get a warning citing the same line number."""
        url_a = "https://example.com/a"
        url_b = "https://example.com/b"
        _set_dead(mock_head, mock_get, {url_a, url_b})
        self._write_md(
            "docs/double.md",
            f"see [one]({url_a}) and [two]({url_b})\n",
        )

        result = self._run_check()

        a_warns = [w for w in result["warned"] if url_a in w]
        b_warns = [w for w in result["warned"] if url_b in w]
        assert len(a_warns) == 1
        assert len(b_warns) == 1
        assert "docs/double.md:1" in a_warns[0]
        assert "docs/double.md:1" in b_warns[0]

    # ------------------------------------------------------------------
    # Pre-release auto-skip behaviour.
    #
    # The test pipeline is created as ``nf-core/testpipeline`` (see
    # ``create_tmp_pipeline``), so the probe URL derived from
    # ``manifest.name`` is ``https://nf-co.re/testpipeline/``. A pre-release
    # state is simulated by making that probe URL 404; a released state by
    # leaving it out of the dead-set (so the default 200 is returned).
    # ------------------------------------------------------------------
    PROBE_URL = "https://nf-co.re/testpipeline/"

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_pre_release_demotes_nf_core_re_404_to_ignored(self, mock_head, mock_get):
        """Probe URL 404 + 404 on ``https://nf-co.re/<short>/...`` -> ignored, not warned."""
        pre_release_url = "https://nf-co.re/testpipeline/results"
        _set_dead(mock_head, mock_get, {self.PROBE_URL, pre_release_url})
        self._write_md("docs/pre.md", f"[results]({pre_release_url})\n")

        result = self._run_check()

        assert not any(pre_release_url in w for w in result["warned"])
        assert any(pre_release_url in m and "Pre-release" in m for m in result["ignored"])

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_pre_release_does_not_demote_unrelated_404(self, mock_head, mock_get):
        """Probe URL 404 (pre-release) + 404 on an unrelated URL -> still warned."""
        unrelated = "https://example.com/dead"
        _set_dead(mock_head, mock_get, {self.PROBE_URL, unrelated})
        self._write_md("docs/u.md", f"[bad]({unrelated})\n")

        result = self._run_check()

        assert any(unrelated in w for w in result["warned"])

    @patch("nf_core.pipelines.lint.broken_links.requests.Session.get")
    @patch("nf_core.pipelines.lint.broken_links.requests.Session.head")
    def test_released_pipeline_warns_on_nf_core_re_404(self, mock_head, mock_get):
        """Probe URL 200 (released) + 404 on ``https://nf-co.re/<short>/...`` -> still warned."""
        sub_url = "https://nf-co.re/testpipeline/results"
        # Probe URL omitted from the dead-set -> returns 200 -> pipeline is "released".
        _set_dead(mock_head, mock_get, {sub_url})
        self._write_md("docs/sub.md", f"[results]({sub_url})\n")

        result = self._run_check()

        assert any(sub_url in w for w in result["warned"])
        assert not any(sub_url in m and "Pre-release" in m for m in result["ignored"])
