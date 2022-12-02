from unittest import mock
import tempfile


@mock.patch("nf_core.modules.ModuleList")
def test_modules_list_remote(self, mock_module_list):
    """Test `nf-core modules list remote`"""
    modules_params = {
        "git-remote": "dummy/remote",
        "branch": "dummy-branch",
        "no-pull": None,
    }

    remote_params = {
        "json": None,
    }

    arg_filters = ("dummy-filter", "dummy2")

    cmd = (
        ["modules"]
        + self.assemble_params(modules_params)
        + ["list", "remote"]
        + self.assemble_params(remote_params)
        + list(arg_filters)
    )
    result = self.invoke_cli(cmd)

    assert result.exit_code == 0
    mock_module_list.assert_called_once_with(
        None,
        True,
        modules_params["git-remote"],
        modules_params["branch"],
        "no-pull" in modules_params,
    )

    mock_module_list.return_value.list_components.assert_called_once_with(arg_filters, "json" in remote_params)


@mock.patch("nf_core.modules.ModuleList")
def test_modules_list_local(self, mock_module_list):
    """Test `nf-core modules list local`"""
    modules_params = {
        "git-remote": "dummy/remote",
        "branch": "dummy-branch",
        "no-pull": None,
    }

    temp_dir = tempfile.TemporaryDirectory()

    local_params = {
        "json": None,
        "dir": temp_dir.name,
    }

    arg_filters = ("dummy-filter", "dummy2")

    cmd = (
        ["modules"]
        + self.assemble_params(modules_params)
        + ["list", "local"]
        + self.assemble_params(local_params)
        + list(arg_filters)
    )
    result = self.invoke_cli(cmd)

    assert result.exit_code == 0
    mock_module_list.assert_called_once_with(
        local_params["dir"],
        False,
        modules_params["git-remote"],
        modules_params["branch"],
        "no-pull" in modules_params,
    )

    mock_module_list.return_value.list_components.assert_called_once_with(arg_filters, "json" in local_params)


def test_critical_errors_are_caught_and_logged(self):
    tests_critical = [
        (["modules", "list", "remote"], "nf_core.modules.ModuleList"),
        (["modules", "remove"], "nf_core.modules.ModuleRemove"),
        (["modules", "create"], "nf_core.modules.ModuleCreate"),
        (["modules", "create-test-yml"], "nf_core.modules.ModulesTestYmlBuilder"),
        (["modules", "lint"], "nf_core.modules.ModuleLint"),
        (["modules", "bump-versions"], "nf_core.modules.bump_versions.ModuleVersionBumper"),
        (["modules", "test"], "nf_core.modules.ModulesTest"),
    ]

    for exception in [UserWarning("error text"), LookupError("error text")]:
        for cmd, patch in tests_critical:
            self.assert_error_is_caught_and_logged(cmd, patch, exception, "CRITICAL")

def test_regular_errors_are_caught_and_logged(self):
    tests_errors = [
        (["modules", "list", "local"], "nf_core.modules.ModuleList"),
        (["modules", "install"], "nf_core.modules.ModuleInstall"),
        (["modules", "update"], "nf_core.modules.ModuleUpdate"),
        (["modules", "patch"], "nf_core.modules.ModulePatch"),
        (["modules", "info"], "nf_core.modules.ModuleInfo"),
    ]

    for exception in [UserWarning("error text"), LookupError("error text")]:
        for cmd, patch in tests_errors:
            self.assert_error_is_caught_and_logged(cmd, patch, exception, "ERROR")
