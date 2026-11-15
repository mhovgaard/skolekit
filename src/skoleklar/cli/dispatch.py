"""Load one command implementation after parsing, including its optional dependencies."""
from importlib import import_module


def handler(module: str, name: str, *, writer: bool = False):
    def run(args):
        path = module if module == "skolekit.cli" else f"skoleklar.cli.{module}"
        command = getattr(import_module(path), name)
        if writer:
            from skoleklar.cli.common import _one_writer

            command = _one_writer(command)
        return command(args)

    run.__name__ = name
    return run
