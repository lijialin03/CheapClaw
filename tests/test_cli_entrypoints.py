import run
from cheapclaw import cli


def test_run_py_forwards_to_cli_main():
    assert run.main is cli.main


def test_cli_model_registry_contains_supported_models():
    assert set(cli.MODEL_CLIENTS) == {"qwen", "deepseek"}
