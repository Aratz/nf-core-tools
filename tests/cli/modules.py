from unittest import mock


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
