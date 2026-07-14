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
"""Unit tests for depends_on.  Run: python3 -m unittest test_depends_on -v"""

import unittest

from depends_on import build_result, has_declaration, parse_dependencies

OS_REPO = "apache/nuttx"
APPS_REPO = "apache/nuttx-apps"
ALLOWED = (OS_REPO, APPS_REPO)


def dep(repo, number):
    return {"repo": repo, "number": number}


class ParseBasicsTest(unittest.TestCase):
    def test_none(self):
        self.assertEqual(parse_dependencies("normal body\nno deps", ALLOWED), ([], []))

    def test_empty(self):
        self.assertEqual(parse_dependencies("", ALLOWED), ([], []))
        self.assertEqual(parse_dependencies(None, ALLOWED), ([], []))

    def test_single(self):
        d, w = parse_dependencies("depends-on: %s/pull/1234" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 1234)])
        self.assertEqual(w, [])

    def test_full_url(self):
        d, _ = parse_dependencies("depends-on: https://github.com/%s/pull/1" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 1)])

    def test_array_mixed(self):
        body = "depends-on: [%s/pull/1 https://github.com/%s/pull/2]" % (APPS_REPO, OS_REPO)
        d, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 1), dep(OS_REPO, 2)])

    def test_dedup(self):
        body = "depends-on: [%s/pull/1 %s/pull/1]" % (APPS_REPO, APPS_REPO)
        d, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 1)])

    def test_case_insensitive(self):
        d, _ = parse_dependencies("Depends-On: %s/pull/7" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 7)])

    def test_non_allowlisted(self):
        d, w = parse_dependencies("depends-on: someone/other/pull/1", ALLOWED)
        self.assertEqual(d, [])
        self.assertTrue(any("unsupported" in x.lower() for x in w))

    def test_typo_push(self):
        d, w = parse_dependencies("depends-on: %s/push/1" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [])
        self.assertTrue(w)

    def test_non_numeric(self):
        d, w = parse_dependencies("depends-on: %s/pull/abc" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [])
        self.assertTrue(w)


class NumberBoundsTest(unittest.TestCase):
    def test_zero_rejected(self):
        self.assertEqual(parse_dependencies("depends-on: %s/pull/0" % APPS_REPO, ALLOWED)[0], [])

    def test_leading_zero_rejected(self):
        self.assertEqual(parse_dependencies("depends-on: %s/pull/007" % APPS_REPO, ALLOWED)[0], [])

    def test_max_15_digits_ok(self):
        d, _ = parse_dependencies("depends-on: %s/pull/123456789012345" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 123456789012345)])

    def test_16_digits_rejected(self):
        self.assertEqual(parse_dependencies("depends-on: %s/pull/1234567890123456" % APPS_REPO, ALLOWED)[0], [])

    def test_huge_number_does_not_crash(self):
        # thousands of digits must be rejected without raising (no int() blowup)
        body = "depends-on: %s/pull/%s" % (APPS_REPO, "9" * 5000)
        d, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(d, [])


class UnicodeLineTest(unittest.TestCase):
    def test_u2028_not_a_new_line(self):
        # U+2028 must NOT be treated as a line break that starts a new marker.
        body = "intro text\u2028depends-on: %s/pull/1" % APPS_REPO
        self.assertFalse(has_declaration(body))
        self.assertEqual(parse_dependencies(body, ALLOWED)[0], [])

    def test_crlf_and_cr_are_line_breaks(self):
        d1, _ = parse_dependencies("x\r\ndepends-on: %s/pull/1" % APPS_REPO, ALLOWED)
        self.assertEqual(d1, [dep(APPS_REPO, 1)])
        d2, _ = parse_dependencies("x\rdepends-on: %s/pull/2" % APPS_REPO, ALLOWED)
        self.assertEqual(d2, [dep(APPS_REPO, 2)])

    def test_u2028_within_declaration_line_not_split(self):
        # On a real depends-on line, a U+2028-joined pair is NOT split into two
        # tokens (ASCII-only separators); it becomes one invalid token.
        body = "depends-on: %s/pull/1\u2028%s/pull/2" % (APPS_REPO, APPS_REPO)
        self.assertEqual(parse_dependencies(body, ALLOWED)[0], [])


class F1LineAnchorTest(unittest.TestCase):
    def test_midline_prose_ignored(self):
        body = "Please see depends-on: %s/pull/13 for the example." % APPS_REPO
        self.assertFalse(has_declaration(body))
        self.assertEqual(parse_dependencies(body, ALLOWED), ([], []))

    def test_not_depends_on_ignored(self):
        body = "not-depends-on: %s/pull/1" % APPS_REPO
        self.assertFalse(has_declaration(body))
        self.assertEqual(parse_dependencies(body, ALLOWED), ([], []))

    def test_code_fence_ignored_backtick(self):
        body = "```\ndepends-on: %s/pull/1\n```" % APPS_REPO
        self.assertFalse(has_declaration(body))
        self.assertEqual(parse_dependencies(body, ALLOWED), ([], []))

    def test_code_fence_ignored_tilde(self):
        body = "~~~\ndepends-on: %s/pull/1\n~~~" % APPS_REPO
        self.assertEqual(parse_dependencies(body, ALLOWED), ([], []))

    def test_leading_whitespace_ok(self):
        d, _ = parse_dependencies("   depends-on: %s/pull/5" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 5)])

    def test_four_space_indent_is_code_ignored(self):
        # 4+ leading spaces = CommonMark indented code block -> ignored.
        body = "    depends-on: %s/pull/1" % APPS_REPO
        self.assertFalse(has_declaration(body))
        self.assertEqual(parse_dependencies(body, ALLOWED), ([], []))

    def test_tab_indent_is_code_ignored(self):
        body = "\tdepends-on: %s/pull/1" % APPS_REPO
        self.assertFalse(has_declaration(body))
        self.assertEqual(parse_dependencies(body, ALLOWED), ([], []))

    def test_indented_fence_is_not_a_fence(self):
        # A ``` indented by 4+ spaces is NOT a fence opener (CommonMark), so the
        # following column-0 depends-on: must still be parsed.
        body = "    ```\ndepends-on: %s/pull/1\n    ```" % APPS_REPO
        d, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 1)])

    def test_real_after_prose_still_parsed(self):
        body = "See docs.\ndepends-on: %s/pull/9\nthanks" % APPS_REPO
        d, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 9)])


class F2HostBoundaryTest(unittest.TestCase):
    def test_gitlab_rejected(self):
        d, w = parse_dependencies("depends-on: gitlab.com/%s/pull/7" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [])
        self.assertTrue(w)  # declaration present but nothing valid

    def test_bare_github_com_without_https_rejected(self):
        d, _ = parse_dependencies("depends-on: github.com/%s/pull/7" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [])

    def test_http_github_rejected(self):
        # only https://github.com/ is accepted as URL form
        d, _ = parse_dependencies("depends-on: http://github.com/%s/pull/7" % APPS_REPO, ALLOWED)
        self.assertEqual(d, [])


class F5MultilineTest(unittest.TestCase):
    def test_bullet_list(self):
        body = (
            "depends-on:\n"
            "- %s/pull/1\n"
            "- https://github.com/%s/pull/2\n" % (APPS_REPO, OS_REPO)
        )
        d, w = parse_dependencies(body, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 1), dep(OS_REPO, 2)])
        self.assertEqual(w, [])

    def test_bullet_list_stops_at_blank(self):
        body = (
            "depends-on:\n"
            "- %s/pull/1\n"
            "\n"
            "- %s/pull/2\n" % (APPS_REPO, APPS_REPO)
        )
        d, _ = parse_dependencies(body, ALLOWED)
        self.assertEqual(d, [dep(APPS_REPO, 1)])  # second bullet after blank not consumed


class BuildResultStatusTest(unittest.TestCase):
    def test_none(self):
        self.assertEqual(build_result("hi", allowed_repos=ALLOWED)["status"], "none")

    def test_ok(self):
        r = build_result("depends-on: %s/pull/9" % APPS_REPO, pr_number=42,
                          head_sha="abc", allowed_repos=ALLOWED)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["dependencies"], [dep(APPS_REPO, 9)])
        self.assertEqual((r["pr_number"], r["head_sha"], r["version"]), (42, "abc", 1))

    def test_invalid(self):
        self.assertEqual(build_result("depends-on: %s/push/1" % APPS_REPO,
                                      allowed_repos=ALLOWED)["status"], "invalid")

    def test_code_fence_is_none(self):
        self.assertEqual(build_result("```\ndepends-on: %s/pull/1\n```" % APPS_REPO,
                                      allowed_repos=ALLOWED)["status"], "none")


if __name__ == "__main__":
    unittest.main()
