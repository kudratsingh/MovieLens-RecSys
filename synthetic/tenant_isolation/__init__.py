"""Cross-tenant leakage canary that can be pointed at a deployed stack.

It lives under ``synthetic/`` rather than ``tests/`` because it is a harness a
deployment runs, not a test a runner collects: the serving image copies
``synthetic/`` and does not copy ``tests/``, so this is the difference between
the verify job proving tenant isolation and reporting that it could not.
"""
