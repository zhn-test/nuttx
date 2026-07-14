#!/usr/bin/env python3
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Parse cross-repo PR dependencies from a NuttX pull request body.

A PR may declare dependencies on other PRs using a ``depends-on:`` line::

    depends-on: [apache/nuttx-apps/pull/1234 https://github.com/apache/nuttx/pull/5678]
    depends-on: apache/nuttx-apps/pull/1234

Rules:

* Only repositories in the allow-list are accepted.  The allow-list is taken
  from the ``NUTTX_REPO`` / ``APPS_REPO`` environment variables so the same
  code works for the test fork and for upstream without editing this file.
* Only a numeric PR id (``/pull/<N>``) is accepted.
* The PR body is untrusted data: it is never executed and only substrings
  matching a strict pattern are extracted.  ``owner/repo`` is limited to safe
  characters so the value is safe to use later in ``git fetch .../pull/<N>``.

Standard-library only and unit tested (``test_depends_on.py``).

CLI:
    python3 depends_on.py                 # print full result JSON
    python3 depends_on.py --print-deps    # print sorted "repo/pull/N" (gate)
    python3 depends_on.py --github-output  # write $GITHUB_OUTPUT + $REPORT_PATH
"""

from __future__ import annotations

import json
import os
import re
import sys

# A single dependency reference: owner/repo/pull/<number>, optionally as a full
# github.com URL.  owner/repo restricted to safe characters.
_DEP_RE = re.compile(
    r"(?:https?://github\.com/)?"
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(?P<num>[0-9]+)"
)

# A "depends-on:" declaration (case-insensitive).
_DEPENDS_ON_RE = re.compile(r"depends-on:[ \t]*(?P<rest>.*)", re.IGNORECASE)


def allowed_repos_from_env():
    """Allow-list of dependency repos, from env (upstream defaults)."""
    return (
        os.environ.get("NUTTX_REPO", "apache/nuttx"),
        os.environ.get("APPS_REPO", "apache/nuttx-apps"),
    )


def has_declaration(body):
    """True if the body contains at least one 'depends-on:' line."""
    return any(_DEPENDS_ON_RE.search(line) for line in (body or "").splitlines())


def parse_dependencies(body, allowed_repos):
    """Return ``(deps, warnings)``.

    ``deps`` is an ordered, de-duplicated list of
    ``{"repo": "owner/repo", "number": <int>}`` limited to ``allowed_repos``.
    ``warnings`` is a list of human-readable messages.
    """
    deps = []
    warnings = []
    seen = set()
    for line in (body or "").splitlines():
        m = _DEPENDS_ON_RE.search(line)
        if not m:
            continue
        for dm in _DEP_RE.finditer(m.group("rest")):
            repo = dm.group("repo")
            num = int(dm.group("num"))
            if repo not in allowed_repos:
                warnings.append("Ignoring unsupported dependency repo: " + repo)
                continue
            key = (repo, num)
            if key not in seen:
                seen.add(key)
                deps.append({"repo": repo, "number": num})

    if has_declaration(body) and not deps:
        warnings.append(
            "Found a 'depends-on:' line but parsed no valid dependency. "
            "Expected: depends-on: [{}/pull/<N> {}/pull/<M>]".format(*allowed_repos)
        )
    return deps, warnings


def build_result(body, pr_number=None, head_sha=None, allowed_repos=None):
    """Build the report object (schema version 1)."""
    if allowed_repos is None:
        allowed_repos = allowed_repos_from_env()
    deps, warnings = parse_dependencies(body, allowed_repos)
    if not has_declaration(body):
        status = "none"          # no depends-on at all -> no report/comment
    elif deps:
        status = "ok"            # at least one valid dependency
    else:
        status = "invalid"       # declared but nothing valid (typo/unsupported)
    return {
        "version": 1,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "status": status,
        "dependencies": deps,
        "warnings": warnings,
    }


def dep_ref(dep):
    return "{}/pull/{}".format(dep["repo"], dep["number"])


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main(argv):
    body = os.environ.get("PR_BODY", "")
    result = build_result(
        body,
        pr_number=_int_or_none(os.environ.get("PR_NUMBER")),
        head_sha=os.environ.get("HEAD_SHA") or None,
    )
    refs = [dep_ref(d) for d in result["dependencies"]]

    if "--print-deps" in argv:
        # Sorted normalized list for change detection (the CI gate).
        for r in sorted(refs):
            print(r)
        return 0

    if "--print-state" in argv:
        # Canonical state for the CI gate: status + sorted dependency refs.
        # Any change here (none<->invalid<->ok, or dependency add/remove/edit)
        # means CI should re-run and (re)produce the report/comment.
        print(result["status"])
        for r in sorted(refs):
            print(r)
        return 0

    if "--github-output" in argv:
        for w in result["warnings"]:
            print("::warning::" + w)
        out_lines = [
            "depends_on=" + " ".join(refs),
            "status=" + result["status"],
        ]
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as f:
                f.write("\n".join(out_lines) + "\n")
        else:
            print("\n".join(out_lines))
        # Write the report whenever a depends-on declaration is present, so the
        # comment workflow can report both success and invalid-format cases.
        report_path = os.environ.get("REPORT_PATH")
        if report_path and result["status"] != "none":
            parent = os.path.dirname(report_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(result, f)
        return 0

    # Default: print the full result JSON.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
