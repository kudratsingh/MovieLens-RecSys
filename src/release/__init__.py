"""Release-time entry points: bootstrap a deployment, verify it, promote into it.

Three modules, none of them long-lived services:

  * ``src.release.bootstrap`` — preflight, schema, seed, materialize. Runs
    before anything serves traffic (the ``release`` job) and, for
    ``materialize``, as the model-server's pre-deploy command.
  * ``src.release.verify`` — the post-deploy verification matrix, run once
    after a release and again on a nightly cron.
  * ``src.release.promote`` — move one tenant's registered champion in
    ``public.tenants`` onto a verified serving bundle. Unlike the other two it
    is run by an operator rather than by a deploy, which is why it prints no
    sentinel: nothing greps it, and a literal here with no counterparty would
    only invite one.

The first two print a sentinel as their final stdout line. A platform that shows
a process which exits as "stopped" or "crashed" gives a deploy gate nothing to
read off the deployment state, so the gate greps the log for the sentinel
instead. Keeping the two literals here means the jobs and whatever greps them
cannot drift apart.

Nothing at this level imports anything heavier than the standard library: the
same source tree is baked into two images with deliberately different
dependency sets (the slim API image has no Feast, pandas or LightGBM; the
features image has no Alembic or httpx), and both import this package.
"""

from __future__ import annotations

RELEASE_BOOTSTRAP_SENTINEL = "RELEASE-BOOTSTRAP-OK"
VERIFY_SENTINEL = "VERIFY-OK"
VERIFY_SUBSET_SENTINEL = "VERIFY-SUBSET-OK"

__all__ = [
    "RELEASE_BOOTSTRAP_SENTINEL",
    "VERIFY_SENTINEL",
    "VERIFY_SUBSET_SENTINEL",
]
