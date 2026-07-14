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

A PR may declare dependencies on other PRs with a ``depends-on:`` line::

    depends-on: apache/nuttx-apps/pull/1234
    depends-on: [apache/nuttx-apps/pull/1 https://github.com/apache/nuttx/pull/2]
    depends-on:
      - apache/nuttx-apps/pull/1
      - https://github.com/apache/nuttx/pull/2

Robustness rules (so documentation / quotes don't trigger false positives):

* The ``depends-on:`` marker must be at the **start of a line** (optional
  leading whitespace only).  This ignores prose like "... depends-on: ..." and
  ``not-depends-on:``.
* Lines inside fenced code blocks (``` or ~~~) are ignored.
* Each dependency is matched **strictly** as ``owner/repo/pull/<n>`` (exactly
  one slash between owner and repo), optionally prefixed with
  ``https://github.com/``.  This rejects other hosts, e.g.
  ``gitlab.com/owner/repo/pull/n``.
* Only repos in the allow-list (NUTTX_REPO / APPS_REPO) and numeric PR ids are
  accepted.

Standard-library only and unit tested (``test_depends_on.py``).

CLI:
    python3 depends_on.py                 # print full result JSON
    python3 depends_on.py --print-deps    # sorted "repo/pull/N" (gate)
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
_TOKEN_RE = re.compile(
    r"^(?:https://github\.com/)?"
    r"(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(?P<num>[0-9]+)$"
)

# "depends-on:" declaration, anchored to the start of the line and
# case-insensitive.  Only up to 3 leading spaces are allowed (no tabs): under
# CommonMark, 4+ leading spaces (or a leading tab) start an indented code block,
# so a deeper-indented "depends-on:" is treated as code and ignored -- matching
# how Zuul anchors its Depends-On footer at the start of a line.  Anchoring also
# avoids matching mid-line prose, quotes, or "not-depends-on:".
_MARKER_RE = re.compile(r"^ {0,3}depends-on:[ \t]*(?P<rest>.*)$", re.IGNORECASE)

# Markdown fenced code block toggle.
_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~)")


def allowed_repos_from_env():
    return (
        os.environ.get("NUTTX_REPO", "apache/nuttx"),
        os.environ.get("APPS_REPO", "apache/nuttx-apps"),
    )


def _split_tokens(text):
    return [t for t in re.split(r"[\s\[\],]+", text.strip()) if t]


def has_declaration(body):
    """True if the body has a line-anchored depends-on: outside code fences."""
    in_fence = False
    for ln in (body or "").splitlines():
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            continue
        if not in_fence and _MARKER_RE.match(ln):
            return True
    return False


def _declared_tokens(body):
    """Yield candidate dependency tokens from every depends-on: declaration,
    skipping fenced code blocks.  Supports same-line declarations and following
    bullet / URL continuation lines."""
    lines = (body or "").splitlines()
    n = len(lines)
    in_fence = False
    i = 0
    while i < n:
        if _FENCE_RE.match(lines[i]):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        m = _MARKER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        toks = _split_tokens(m.group("rest"))
        # Continuation: following non-blank lines whose (bullet-stripped) tokens
        # are all valid dependency refs, until a blank/other/fence/marker line.
        j = i + 1
        while (j < n and lines[j].strip()
               and not _FENCE_RE.match(lines[j])
               and not _MARKER_RE.match(lines[j])):
            stripped = re.sub(r"^[ \t]*[-*][ \t]*", "", lines[j].strip())
            cont = _split_tokens(stripped)
            if cont and all(_TOKEN_RE.match(t) for t in cont):
                toks.extend(cont)
                j += 1
            else:
                break
        for t in toks:
            yield t
        i = j


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
            "Declare dependencies on the same line as 'depends-on:' (or on "
            "following '- <ref>' lines), e.g. "
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

    if "--print-deps" in argv:
        for r in sorted(refs):
            print(r)
        return 0

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
        # Always write the report (including status=none) so the comment
        # workflow can also CLEAR a stale comment when depends-on is removed.
        report_path = os.environ.get("REPORT_PATH")
        if report_path:
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
