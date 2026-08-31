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
"""Unit tests for depends_on.

Run with:  python3 -m unittest test_depends_on -v
"""

import unittest

from depends_on import build_result, parse_dependencies

# Deterministic allow-list for tests (independent of environment).
OS_REPO = "apache/nuttx"
APPS_REPO = "apache/nuttx-apps"
ALLOWED = (OS_REPO, APPS_REPO)


def dep(repo, number):
    return {"repo": repo, "number": number}


class ParseDependenciesTest(unittest.TestCase):
    def test_no_depends_on(self):
        deps, warnings = parse_dependencies("Normal PR body.\nNo deps.", ALLOWED)
        self.assertEqual(deps, [])
        self.assertEqual(warnings, [])

    def test_empty_body(self):
        self.assertEqual(parse_dependencies("", ALLOWED), ([], []))
        self.assertEqual(parse_dependencies(None, ALLOWED), ([], []))

    def test_valid_shorthand(self):
        deps, warnings = parse_dependencies("depends-on: %s/pull/1234" % APPS_REPO, ALLOWED)
        self.assertEqual(deps, [dep(APPS_REPO, 1234)])
        self.assertEqual(warnings, [])

    def test_valid_full_url(self):
        deps, _ = parse_dependencies(
            "depends-on: https://github.com/%s/pull/1234" % APPS_REPO, ALLOWED
        )
        self.assertEqual(deps, [dep(APPS_REPO, 1234)])

    def test_array_multiple_mixed(self):
        body = "depends-on: [%s/pull/1 https://github.com/%s/pull/2]" % (APPS_REPO, OS_REPO)
        deps, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(deps, [dep(APPS_REPO, 1), dep(OS_REPO, 2)])

    def test_dedup_preserves_order(self):
        body = "depends-on: [%s/pull/1 %s/pull/1 %s/pull/2]" % (APPS_REPO, APPS_REPO, APPS_REPO)
        deps, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(deps, [dep(APPS_REPO, 1), dep(APPS_REPO, 2)])

    def test_case_insensitive_marker(self):
        deps, _ = parse_dependencies("Depends-On: %s/pull/7" % APPS_REPO, ALLOWED)
        self.assertEqual(deps, [dep(APPS_REPO, 7)])

    def test_non_allowlisted_repo_warns(self):
        deps, warnings = parse_dependencies("depends-on: someone/other/pull/1", ALLOWED)
        self.assertEqual(deps, [])
        self.assertTrue(any("unsupported" in w.lower() for w in warnings))

    def test_typo_push_not_pull(self):
        deps, warnings = parse_dependencies("depends-on: %s/push/1" % APPS_REPO, ALLOWED)
        self.assertEqual(deps, [])
        self.assertTrue(warnings)

    def test_non_numeric_pr_id(self):
        deps, warnings = parse_dependencies("depends-on: %s/pull/abc" % APPS_REPO, ALLOWED)
        self.assertEqual(deps, [])
        self.assertTrue(warnings)


class BuildResultTest(unittest.TestCase):
    def test_status_none(self):
        r = build_result("no dependencies here", allowed_repos=ALLOWED)
        self.assertEqual(r["status"], "none")
        self.assertEqual(r["dependencies"], [])
        self.assertEqual(r["version"], 1)

    def test_status_ok(self):
        r = build_result(
            "depends-on: %s/pull/9" % APPS_REPO,
            pr_number=42,
            head_sha="abc123",
            allowed_repos=ALLOWED,
        )
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["dependencies"], [dep(APPS_REPO, 9)])
        self.assertEqual(r["pr_number"], 42)
        self.assertEqual(r["head_sha"], "abc123")

    def test_status_invalid_typo(self):
        r = build_result("depends-on: %s/push/1" % APPS_REPO, allowed_repos=ALLOWED)
        self.assertEqual(r["status"], "invalid")
        self.assertEqual(r["dependencies"], [])
        self.assertTrue(r["warnings"])

    def test_status_invalid_unsupported_repo(self):
        r = build_result("depends-on: other/repo/pull/1", allowed_repos=ALLOWED)
        self.assertEqual(r["status"], "invalid")

    def test_status_invalid_empty_declaration(self):
        r = build_result("depends-on:", allowed_repos=ALLOWED)
        self.assertEqual(r["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
