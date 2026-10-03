"""Pytest plugins that are not tests.

Kept as a package so the modules are addressable as ``tests.plugins.<name>``
and loaded explicitly with ``-p`` (``python -m pytest`` puts the repository
root on ``sys.path``). Files here are never collected as tests.
"""
