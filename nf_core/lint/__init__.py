#!/usr/bin/env python
"""Linting policy for nf-core pipeline projects.

Tests Nextflow-based pipelines to check that they adhere to
the nf-core community guidelines.
"""

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
import datetime
import git
import json
import logging
import os
import re
import rich
import rich.progress
import textwrap
import yaml

import nf_core.utils

log = logging.getLogger(__name__)


def run_linting(pipeline_dir, release_mode=False, show_passed=False, md_fn=None, json_fn=None):
    """Runs all nf-core linting checks on a given Nextflow pipeline project
    in either `release` mode or `normal` mode (default). Returns an object
    of type :class:`PipelineLint` after finished.

    Args:
        pipeline_dir (str): The path to the Nextflow pipeline root directory
        release_mode (bool): Set this to `True`, if the linting should be run in the `release` mode.
                             See :class:`PipelineLint` for more information.

    Returns:
        An object of type :class:`PipelineLint` that contains all the linting results.
    """

    # Create the lint object
    lint_obj = PipelineLint(pipeline_dir, release_mode)

    # Load the pipeline lint config, if there is one
    lint_obj._parse_lint_config()

    # Parse the pipeline Nextflow config
    lint_obj._get_pipeline_config()

    # Run the linting tests
    try:
        lint_obj._lint_pipeline()
    except AssertionError as e:
        log.critical("Critical error: {}".format(e))
        log.info("Stopping tests...")
        return lint_obj

    # Print the results
    lint_obj._print_results(show_passed)

    # Save results to Markdown file
    if md_fn is not None:
        log.info("Writing lint results to {}".format(md_fn))
        markdown = lint_obj._get_results_md()
        with open(md_fn, "w") as fh:
            fh.write(markdown)

    # Save results to JSON file
    if json_fn is not None:
        lint_obj._save_json_results(json_fn)

    # Exit code
    if len(lint_obj.failed) > 0:
        if release_mode:
            log.info("Reminder: Lint tests were run in --release mode.")

    return lint_obj


class PipelineLint(object):
    """Object to hold linting information and results.

    Use the :func:`PipelineLint.lint_pipeline` function to run lint tests.

    Args:
        path (str): The path to the nf-core pipeline directory.

    Attributes:
        conda_config (dict): The parsed conda configuration file content (`environment.yml`).
        conda_package_info (dict): The conda package(s) information, based on the API requests to Anaconda cloud.
        config (dict): The Nextflow pipeline configuration file content.
        dockerfile (list): A list of lines (str) from the parsed Dockerfile.
        failed (list): A list of tuples of the form: `(<test-name>, <reason>)`
        files (list): A list of files found during the linting process.
        git_sha (str): The git sha for the repo commit / current GitHub pull-request (`$GITHUB_PR_COMMIT`)
        ignored (list): A list of tuples of the form: `(<test-name>, <reason>)`
        lint_config (dict): The parsed nf-core linting config for this pipeline
        minNextflowVersion (str): The minimum required Nextflow version to run the pipeline.
        passed (list): A list of tuples of the form: `(<test-name>, <reason>)`
        path (str): Path to the pipeline directory.
        pipeline_name (str): The pipeline name, without the `nf-core` tag, for example `hlatyping`.
        release_mode (bool): `True`, if you the to linting was run in release mode, `False` else.
        schema_obj (obj): A :class:`PipelineSchema` object
        version (str): The version number of nf-core/tools (to allow modification for testing)
        warned (list): A list of tuples of the form: `(<warned no>, <reason>)`

    **Attribute specifications**

    Some of the more complex attributes of a PipelineLint object.

    * `conda_config`::

        # Example
         {
            'name': 'nf-core-hlatyping',
            'channels': ['bioconda', 'conda-forge'],
            'dependencies': ['optitype=1.3.2', 'yara=0.9.6']
         }

    * `conda_package_info`::

        # See https://api.anaconda.org/package/bioconda/bioconda-utils as an example.
         {
            <package>: <API JSON repsonse object>
         }

    * `config`: Produced by calling Nextflow with :code:`nextflow config -flat <workflow dir>`. Here is an example from
        the `nf-core/hlatyping <https://github.com/nf-core/hlatyping>`_ pipeline::

            process.container = 'nfcore/hlatyping:1.1.1'
            params.help = false
            params.outdir = './results'
            params.bam = false
            params.single_end = false
            params.seqtype = 'dna'
            params.solver = 'glpk'
            params.igenomes_base = './iGenomes'
            params.clusterOptions = false
            ...
    """

    from .files_exist import files_exist
    from .docker import docker
    from .licence import licence
    from .nextflow_config import nextflow_config
    from .actions_branch_protection import actions_branch_protection
    from .actions_ci import actions_ci
    from .actions_lint import actions_lint
    from .actions_awstest import actions_awstest
    from .actions_awsfulltest import actions_awsfulltest
    from .readme import readme
    from .version_consistency import version_consistency
    from .conda_env_yaml import conda_env_yaml, _anaconda_package, _pip_package
    from .conda_dockerfile import conda_dockerfile
    from .pipeline_todos import pipeline_todos
    from .pipeline_name_conventions import pipeline_name_conventions
    from .cookiecutter_strings import cookiecutter_strings
    from .schema_lint import schema_lint
    from .schema_params import schema_params

    def __init__(self, path, release_mode=False):
        """ Initialise linting object """
        self.conda_config = {}
        self.conda_package_info = {}
        self.config = {}
        self.dockerfile = []
        self.failed = []
        self.files = []
        self.git_sha = None
        self.ignored = []
        self.lint_config = {}
        self.minNextflowVersion = None
        self.passed = []
        self.path = path
        self.pipeline_name = None
        self.release_mode = False
        self.schema_obj = None
        self.version = nf_core.__version__
        self.warned = []

        self.lint_tests = [
            "files_exist",
            "licence",
            "docker",
            "nextflow_config",
            "actions_branch_protection",
            "actions_ci",
            "actions_lint",
            "actions_awstest",
            "actions_awsfulltest",
            "readme",
            "conda_env_yaml",
            "conda_dockerfile",
            "pipeline_todos",
            "pipeline_name_conventions",
            "cookiecutter_strings",
            "schema_lint",
            "schema_params",
        ]
        if self.release_mode:
            self.lint_tests.extend(["version_consistency"])

        try:
            repo = git.Repo(self.path)
            self.git_sha = repo.head.object.hexsha
        except:
            pass

        # Overwrite if we have the last commit from the PR - otherwise we get a merge commit hash
        if os.environ.get("GITHUB_PR_COMMIT", "") != "":
            self.git_sha = os.environ["GITHUB_PR_COMMIT"]

    def _parse_lint_config(self):
        """Parse a pipeline lint config file.

        Look for a file called either `.nf-core-lint.yml` or
        `.nf-core-lint.yaml` in the pipeline root directory and parse it.
        (`.yml` takes precedence).

        Add parsed config to the `self.lint_config` class attribute.
        """
        config_fn = os.path.join(self.path, ".nf-core-lint.yml")

        # Pick up the file if it's .yaml instead of .yml
        if not os.path.isfile(config_fn):
            config_fn = os.path.join(self.path, ".nf-core-lint.yaml")

        # Load the YAML
        try:
            with open(config_fn, "r") as fh:
                self.lint_config = yaml.safe_load(fh)
        except FileNotFoundError:
            log.debug("No lint config file found: {}".format(config_fn))

        # Check if we have any keys that don't match lint test names
        for k in self.lint_config:
            if k not in self.lint_tests:
                log.warn("Found unrecognised test name '{}' in pipeline lint config".format(k))

    def _get_pipeline_config(self):
        """Get the nextflow config for this pipeline"""
        self.config = nf_core.utils.fetch_wf_config(self.path)

    def _lint_pipeline(self):
        """Main linting function.

        Takes the pipeline directory as the primary input and iterates through
        the different linting checks in order. Collects any warnings or errors
        and returns summary at completion. Raises an exception if there is a
        critical error that makes the rest of the tests pointless (eg. no
        pipeline script). Results from this function are printed by the main script.

        Args:
            release_mode (boolean): Activates the release mode, which checks for
                consistent version tags of containers. Default is `False`.

        Returns:
            dict: Summary of test result messages structured as follows::

            {
                'pass': [
                    ( test-id (int), message (string) ),
                    ( test-id (int), message (string) )
                ],
                'warn': [(id, msg)],
                'fail': [(id, msg)],
            }

        Raises:
            If a critical problem is found, an ``AssertionError`` is raised.
        """
        log.info("Testing pipeline: [magenta]{}".format(self.path))
        if self.release_mode:
            log.info("Including --release mode tests")

        progress = rich.progress.Progress(
            "[bold blue]{task.description}",
            rich.progress.BarColumn(bar_width=None),
            "[magenta]{task.completed} of {task.total}[reset] » [bold yellow]{task.fields[func_name]}",
            transient=True,
        )
        with progress:
            lint_progress = progress.add_task(
                "Running lint checks", total=len(self.lint_tests), func_name=self.lint_tests[0]
            )
            for fun_name in self.lint_tests:
                if self.lint_config.get(fun_name) is False:
                    log.debug("Skipping lint test '{}'".format(fun_name))
                    self.ignored.append((fun_name, fun_name))
                    continue
                progress.update(lint_progress, advance=1, func_name=fun_name)
                log.debug("Running lint test: {}".format(fun_name))
                test_results = getattr(self, fun_name)()
                for test in test_results.get("passed", []):
                    self.passed.append((fun_name, test))
                for test in test_results.get("warned", []):
                    self.warned.append((fun_name, test))
                for test in test_results.get("failed", []):
                    self.failed.append((fun_name, test))

    def _print_results(self, show_passed=False):

        log.debug("Printing final results")
        console = Console(force_terminal=nf_core.utils.rich_force_colors())

        # Helper function to format test links nicely
        def format_result(test_results, table):
            """
            Given an list of error message IDs and the message texts, return a nicely formatted
            string for the terminal with appropriate ASCII colours.
            """
            for eid, msg in test_results:
                table.add_row(Markdown("[{0}](https://nf-co.re/errors#{0}): {1}".format(eid, msg)))
            return table

        def _s(some_list):
            if len(some_list) > 1:
                return "s"
            return ""

        # Table of passed tests
        if len(self.passed) > 0 and show_passed:
            table = Table(style="green", box=rich.box.ROUNDED)
            table.add_column(
                r"\[✔] {} Test{} Passed".format(len(self.passed), _s(self.passed)),
                no_wrap=True,
            )
            table = format_result(self.passed, table)
            console.print(table)

        # Table of ignored tests
        if len(self.ignored) > 0:
            table = Table(style="grey58", box=rich.box.ROUNDED)
            table.add_column(r"\[?] {} Test{} Ignored".format(len(self.ignored), _s(self.ignored)), no_wrap=True)
            table = format_result(self.ignored, table)
            console.print(table)

        # Table of warning tests
        if len(self.warned) > 0:
            table = Table(style="yellow", box=rich.box.ROUNDED)
            table.add_column(r"\[!] {} Test Warning{}".format(len(self.warned), _s(self.warned)), no_wrap=True)
            table = format_result(self.warned, table)
            console.print(table)

        # Table of failing tests
        if len(self.failed) > 0:
            table = Table(style="red", box=rich.box.ROUNDED)
            table.add_column(
                r"\[✗] {} Test{} Failed".format(len(self.failed), _s(self.failed)),
                no_wrap=True,
            )
            table = format_result(self.failed, table)
            console.print(table)

        # Summary table
        table = Table(box=rich.box.ROUNDED)
        table.add_column("[bold green]LINT RESULTS SUMMARY".format(len(self.passed)), no_wrap=True)
        table.add_row(
            r"\[✔] {:>3} Test{} Passed".format(len(self.passed), _s(self.passed)),
            style="green",
        )
        table.add_row(r"\[?] {:>3} Test{} Ignored".format(len(self.ignored), _s(self.ignored)), style="grey58")
        table.add_row(r"\[!] {:>3} Test Warning{}".format(len(self.warned), _s(self.warned)), style="yellow")
        table.add_row(r"\[✗] {:>3} Test{} Failed".format(len(self.failed), _s(self.failed)), style="red")
        console.print(table)

    def _get_results_md(self):
        """
        Function to create a markdown file suitable for posting in a GitHub comment
        """
        # Overall header
        overall_result = "Passed :white_check_mark:"
        if len(self.failed) > 0:
            overall_result = "Failed :x:"

        # List of tests for details
        test_failure_count = ""
        test_failures = ""
        if len(self.failed) > 0:
            test_failure_count = "\n-| ❌ {:3d} tests failed       |-".format(len(self.failed))
            test_failures = "### :x: Test failures:\n\n{}\n\n".format(
                "\n".join(
                    [
                        "* [{0}](https://nf-co.re/errors#{0}) - {1}".format(eid, self._strip_ansi_codes(msg, "`"))
                        for eid, msg in self.failed
                    ]
                )
            )

        test_ignored_count = ""
        test_ignored = ""
        if len(self.ignored) > 0:
            test_ignored_count = "\n#| ❔ {:3d} tests had warnings |#".format(len(self.ignored))
            test_ignored = "### :grey_question: Tests ignored:\n\n{}\n\n".format(
                "\n".join(
                    [
                        "* [{0}](https://nf-co.re/errors#{0}) - {1}".format(eid, self._strip_ansi_codes(msg, "`"))
                        for eid, msg in self.ignored
                    ]
                )
            )

        test_warning_count = ""
        test_warnings = ""
        if len(self.warned) > 0:
            test_warning_count = "\n!| ❗ {:3d} tests had warnings |!".format(len(self.warned))
            test_warnings = "### :heavy_exclamation_mark: Test warnings:\n\n{}\n\n".format(
                "\n".join(
                    [
                        "* [{0}](https://nf-co.re/errors#{0}) - {1}".format(eid, self._strip_ansi_codes(msg, "`"))
                        for eid, msg in self.warned
                    ]
                )
            )

        test_passed_count = ""
        test_passes = ""
        if len(self.passed) > 0:
            test_passed_count = "\n+| ✅ {:3d} tests passed       |+".format(len(self.passed))
            test_passes = "### :white_check_mark: Tests passed:\n\n{}\n\n".format(
                "\n".join(
                    [
                        "* [{0}](https://nf-co.re/errors#{0}) - {1}".format(eid, self._strip_ansi_codes(msg, "`"))
                        for eid, msg in self.passed
                    ]
                )
            )

        now = datetime.datetime.now()

        markdown = textwrap.dedent(
            """
        #### `nf-core lint` overall result: {}

        {}

        ```diff{}{}{}{}
        ```

        <details>

        {}{}{}{}### Run details:

        * nf-core/tools version {}
        * Run at `{}`

        </details>
        """
        ).format(
            overall_result,
            "Posted for pipeline commit {}".format(self.git_sha[:7]) if self.git_sha is not None else "",
            test_passed_count,
            test_ignored_count,
            test_warning_count,
            test_failure_count,
            test_failures,
            test_warnings,
            test_ignored,
            test_passes,
            nf_core.__version__,
            now.strftime("%Y-%m-%d %H:%M:%S"),
        )

        return markdown

    def _save_json_results(self, json_fn):
        """
        Function to dump lint results to a JSON file for downstream use
        """

        log.info("Writing lint results to {}".format(json_fn))
        now = datetime.datetime.now()
        results = {
            "nf_core_tools_version": nf_core.__version__,
            "date_run": now.strftime("%Y-%m-%d %H:%M:%S"),
            "tests_pass": [[idx, self._strip_ansi_codes(msg)] for idx, msg in self.passed],
            "tests_ignored": [[idx, self._strip_ansi_codes(msg)] for idx, msg in self.ignored],
            "tests_warned": [[idx, self._strip_ansi_codes(msg)] for idx, msg in self.warned],
            "tests_failed": [[idx, self._strip_ansi_codes(msg)] for idx, msg in self.failed],
            "num_tests_pass": len(self.passed),
            "num_tests_ignored": len(self.ignored),
            "num_tests_warned": len(self.warned),
            "num_tests_failed": len(self.failed),
            "has_tests_pass": len(self.passed) > 0,
            "has_tests_warned": len(self.warned) > 0,
            "has_tests_failed": len(self.failed) > 0,
            "markdown_result": self._get_results_md(),
        }
        with open(json_fn, "w") as fh:
            json.dump(results, fh, indent=4)

    def _wrap_quotes(self, files):
        if not isinstance(files, list):
            files = [files]
        bfiles = ["`{}`".format(f) for f in files]
        return " or ".join(bfiles)

    def _strip_ansi_codes(self, string, replace_with=""):
        # https://stackoverflow.com/a/14693789/713980
        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        return ansi_escape.sub(replace_with, string)
