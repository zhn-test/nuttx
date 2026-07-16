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

A PR may declare dependencies on other PRs with a ``depends-on:`` line.
Each declaration is a SINGLE line; use an inline list or several
``depends-on:`` lines for multiple dependencies (bullet lists are NOT
supported)::

    depends-on: apache/nuttx-apps/pull/1234
    depends-on: [apache/nuttx-apps/pull/1 https://github.com/apache/nuttx/pull/2]

    depends-on: apache/nuttx-apps/pull/1
    depends-on: https://github.com/apache/nuttx/pull/2

Robustness rules (so documentation / quotes don't trigger false positives):

* The ``depends-on:`` marker must be at the **start of a line**, with at most
  3 leading spaces and no tab (4+ spaces or a tab is a CommonMark indented code
  block).  This ignores prose like "... depends-on: ..." and ``not-depends-on:``.
* Lines inside fenced code blocks (``` or ~~~) are ignored.
* Body lines are split on standard newlines only (\n, \r\n, \r); tokens on a
  declaration line are split on ASCII whitespace only.
* Each dependency is matched **strictly** as ``owner/repo/pull/<n>`` (exactly
  one slash between owner and repo), optionally prefixed with
  ``https://github.com/``.  This rejects other hosts, e.g.
  ``gitlab.com/owner/repo/pull/n``.
* The PR number is a positive integer (no leading zero) within JS
  Number.MAX_SAFE_INTEGER.
* Only repos in the allow-list (NUTTX_REPO / APPS_REPO) are accepted.

Standard-library only and unit tested (``test_depends_on.py``).

CLI:
    python3 depends_on.py                 # print full result JSON
    python3 depends_on.py --print-state   # status + sorted deps (gate)
    python3 depends_on.py --github-output # write $GITHUB_OUTPUT + $REPORT_PATH
"""

from __future__ import annotations

import json
import os
import re
import sys

# A single dependency token: owner/repo/pull/<n>, optionally as a full
# github.com URL.  owner and repo are single path segments (exactly one slash
# between them), which rejects other hosts such as
# gitlab.com/owner/repo/pull/n (two slashes) and bare github.com/... (no https).
# The PR number is a positive integer with no leading zero.  GitHub does not
# document a maximum PR/issue number, so we do NOT impose a business cap; the
# only real constraint is that the comment workflow handles this value in
# JavaScript, where integers above Number.MAX_SAFE_INTEGER (2**53-1) cannot be
# represented exactly.  So the digit count is bounded (<=19) purely to stop a
# huge-digit string from reaching int() (Python 3.11+ raises on very long int()
# conversions), and the exact ceiling is enforced numerically against
# MAX_SAFE_INTEGER (matching the comment workflow's Number.isSafeInteger check).
_MAX_SAFE_PR_NUMBER = 9007199254740991  # 2**53 - 1 == JS Number.MAX_SAFE_INTEGER
_TOKEN_RE = re.compile(
    r"^(?:https://github\.com/)?"
    r"(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(?P<num>[1-9][0-9]{0,18})$"
)

# "depends-on:" declaration, anchored to the start of the line and
# case-insensitive.  Only up to 3 leading spaces are allowed (no tabs): under
# CommonMark, 4+ leading spaces (or a leading tab) start an indented code block,
# so a deeper-indented "depends-on:" is treated as code and ignored -- matching
# how Zuul anchors its Depends-On footer at the start of a line.  Anchoring also
# avoids matching mid-line prose, quotes, or "not-depends-on:".
_MARKER_RE = re.compile(r"^ {0,3}depends-on:[ \t]*(?P<rest>.*)$", re.IGNORECASE)

# Markdown fenced code block toggle.  Per CommonMark a fence opener may be
# indented at most 3 spaces (4+ spaces or a tab would be an indented code
# block), so keep this threshold aligned with _MARKER_RE.
_FENCE_RE = re.compile(r"^ {0,3}(?:```|~~~)")


def allowed_repos_from_env():
    return (
        os.environ.get("NUTTX_REPO", "apache/nuttx"),
        os.environ.get("APPS_REPO", "apache/nuttx-apps"),
    )


def _split_tokens(text):
    # ASCII-only separators (space/tab/newline/brackets/comma).  \s would also
    # match Unicode separators (U+2028 etc.); keeping to ASCII (like _lines)
    # means a Unicode-separated string is treated as one (invalid) token rather
    # than silently split into several dependencies on a single declaration line.
    return [t for t in re.split(r"[ \t\r\n\[\],]+", text.strip()) if t]


def _lines(body):
    """Split into lines on standard newlines only (\\n, \\r\\n, \\r).

    str.splitlines() also breaks on Unicode separators (U+2028/U+2029/U+0085,
    vertical tab, form feed) that GitHub Markdown does NOT render as line
    breaks; relying on it could let ordinary body text be mis-read as a new
    line-anchored 'depends-on:' declaration.
    """
    return (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def has_declaration(body):
    """True if the body has a line-anchored depends-on: outside code fences."""
    in_fence = False
    for ln in _lines(body):
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            continue
        if not in_fence and _MARKER_RE.match(ln):
            return True
    return False


def _declared_tokens(body):
    """Yield candidate dependency tokens from every line-anchored depends-on:
    declaration, skipping fenced code blocks.  Each declaration is a SINGLE
    line: ``depends-on: <ref>`` or ``depends-on: [<ref> <ref> ...]``.  Multiple
    dependencies use an inline list or several ``depends-on:`` lines."""
    in_fence = False
    for ln in _lines(body):
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _MARKER_RE.match(ln)
        if m:
            for t in _split_tokens(m.group("rest")):
                yield t


def parse_dependencies(body, allowed_repos):
    """Return ``(deps, warnings)``; deps = ordered de-duplicated
    ``{"repo","number"}`` limited to ``allowed_repos``."""
    deps = []
    warnings = []
    seen = set()
    for t in _declared_tokens(body):
        m = _TOKEN_RE.match(t)
        if not m:
            continue  # stray text on a declaration line; ignore
        repo = m.group("repo")
        num = int(m.group("num"))
        if num > _MAX_SAFE_PR_NUMBER:
            continue  # beyond JS safe integer; comment workflow can't handle it
        if repo not in allowed_repos:
            warnings.append("Ignoring unsupported dependency repo: " + repo)
            continue
        key = (repo, num)
        if key not in seen:
            seen.add(key)
            deps.append({"repo": repo, "number": num})

    if has_declaration(body) and not deps:
        warnings.append(
            "Found a 'depends-on:' line but no valid dependency was parsed. "
            "Declare dependencies on the same line as 'depends-on:', e.g. "
            "depends-on: [{}/pull/<N> {}/pull/<M>]".format(*allowed_repos)
        )
    return deps, warnings


def build_result(body, pr_number=None, head_sha=None, allowed_repos=None):
    if allowed_repos is None:
        allowed_repos = allowed_repos_from_env()
    deps, warnings = parse_dependencies(body, allowed_repos)
    if not has_declaration(body):
        status = "none"
    elif deps:
        status = "ok"
    else:
        status = "invalid"
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

    if "--print-state" in argv:
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
        # Write the report only when a depends-on declaration is present
        # (status ok/invalid).  status=none means there is nothing to report and
        # nothing to comment; existing (historical) comments are left untouched.
        report_path = os.environ.get("REPORT_PATH")
        if report_path and result["status"] != "none":
            parent = os.path.dirname(report_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(result, f)
        return 0

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
