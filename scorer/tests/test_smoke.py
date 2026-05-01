"""Smoke tests — package imports and CLI parses."""

from __future__ import annotations


def test_package_import():
    import apodornot

    assert apodornot.__version__


def test_cli_parses_help():
    from apodornot.cli import _build_parser

    parser = _build_parser()
    assert parser.prog == "apodornot"
