from pathlib import Path
from unittest import mock

import requests
import yaml

import nf_core.pipelines.lint

from ..test_lint import TestLint


class TestLintActionsSchemaValidation(TestLint):
    def setUp(self) -> None:
        super().setUp()
        self.new_pipeline = self._make_pipeline_copy()
        self.awstest_yml_path = Path(self.new_pipeline) / ".github" / "workflows" / "awstest.yml"
        with open(self.awstest_yml_path) as fh:
            self.awstest_yml = yaml.safe_load(fh)

    def test_actions_schema_validation_missing_jobs(self):
        """Missing 'jobs' field should result in failure"""

        self.awstest_yml.pop("jobs")
        with open(self.awstest_yml_path, "w") as fh:
            yaml.dump(self.awstest_yml, fh)

        lint_obj = nf_core.pipelines.lint.PipelineLint(self.new_pipeline)
        lint_obj._load()

        results = lint_obj.actions_schema_validation()

        assert "Workflow validation failed for awstest.yml: 'jobs' is a required property" in results["failed"][0]

    def test_actions_schema_validation_missing_on(self):
        """Missing 'on' field should result in failure"""

        self.awstest_yml.pop(True)
        with open(self.awstest_yml_path, "w") as fh:
            yaml.dump(self.awstest_yml, fh)

        lint_obj = nf_core.pipelines.lint.PipelineLint(self.new_pipeline)
        lint_obj._load()

        results = lint_obj.actions_schema_validation()

        assert results["failed"][0] == "Missing 'on' keyword in awstest.yml"
        assert "Workflow validation failed for awstest.yml: 'on' is a required property" in results["failed"][1]

    def test_actions_schema_validation_fails_for_additional_property(self):
        """Missing 'jobs' field should result in failure"""

        self.awstest_yml["not_jobs"] = self.awstest_yml["jobs"]
        with open(self.awstest_yml_path, "w") as fh:
            yaml.dump(self.awstest_yml, fh)

        lint_obj = nf_core.pipelines.lint.PipelineLint(self.new_pipeline)
        lint_obj._load()

        results = lint_obj.actions_schema_validation()

        assert (
            "Workflow validation failed for awstest.yml: Additional properties are not allowed ('not_jobs' was unexpected)"
            in results["failed"][0]
        )

    def test_actions_schema_validation_timeout_warns(self):
        """A timeout when fetching the schema should result in a warning, not a hang or crash"""

        lint_obj = nf_core.pipelines.lint.PipelineLint(self.new_pipeline)
        lint_obj._load()

        with mock.patch("requests.get", side_effect=requests.exceptions.Timeout("timed out")) as mock_get:
            results = lint_obj.actions_schema_validation()

        assert mock_get.call_args.kwargs["timeout"] == 10
        assert results["failed"] == []
        assert "Failed to fetch schema" in results["warned"][0]
