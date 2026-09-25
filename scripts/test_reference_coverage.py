"""Tests for scripts/reference_coverage.py.

    python scripts/test_reference_coverage.py -v

Every test builds a throw-away git repository, so nothing here touches (or depends on) the real reference tree.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_coverage as rc  # noqa: E402


def git(repo, *args, check=True):
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
         "-c", "core.autocrlf=false", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout.strip()


def write(repo, rel, content):
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def lines(items):
    return "\n".join(items) + "\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RepoCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        git(self.repo, "init", "-q", "-b", "main")

    def commit_all(self, message):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")


class VerifyTests(RepoCase):
    def setUp(self):
        super().setUp()
        write(self.repo, "a.txt", lines(["one", "two", "three"]))
        write(self.repo, "b.txt", lines(["bee"]))
        write(self.repo, "sub/c.txt", lines(["see"]))
        self.head = self.commit_all("c0")
        write(self.repo, "a.txt", lines(["one", "two", "three", "four"]))  # ' M'
        write(self.repo, "sub/new.txt", lines(["fresh"]))  # '??'
        self.manifest = rc.build_manifest(self.repo, self.head, {"baseline": self.head})

    def test_manifest_lists_sorted_paths_with_hashes_and_the_pinned_rules(self):
        m = self.manifest
        self.assertEqual([p["path"] for p in m["paths"]], ["a.txt", "sub/new.txt"])
        modified, untracked = m["paths"]
        self.assertEqual(modified["status"], "M")
        self.assertEqual(modified["worktree_sha256"], sha256(lines(["one", "two", "three", "four"]).encode()))
        self.assertEqual(modified["head_blob"], git(self.repo, "rev-parse", "HEAD:a.txt"))
        self.assertRegex(modified["diff_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(untracked["status"], "??")
        self.assertIsNone(untracked["head_blob"])
        self.assertIsNone(untracked["diff_sha256"])
        self.assertEqual(m["reference"]["head"], self.head)
        self.assertEqual(m["targets"], {"baseline": self.head})
        for pin in ("core.autocrlf=false", "--unified=0", "--no-renames", "--diff-algorithm=myers", "--full-index"):
            self.assertIn(pin, m["diff_command"])
        for key in ("input_sha256", "diff_input_sha256", "manifest_sha256", "rules", "python", "git", "script"):
            self.assertIn(key, m)

    def test_manifest_is_deterministic(self):
        again = rc.build_manifest(self.repo, self.head, {"baseline": self.head})
        self.assertEqual(again, self.manifest)

    def test_manifest_sorts_paths_bytewise(self):
        # 'Z' (0x5A) sorts before 'a' (0x61) bytewise; a case-insensitive sort would put a.txt first. (The name must
        # not be a case variant of a tracked file: on Windows that would just rewrite the tracked one.)
        write(self.repo, "Zed.txt", "upper\n")
        m = rc.build_manifest(self.repo, self.head, {"baseline": self.head})
        self.assertEqual([p["path"] for p in m["paths"]], ["Zed.txt", "a.txt", "sub/new.txt"])

    def test_abbreviated_revisions_are_stored_as_full_shas(self):
        m = rc.build_manifest(self.repo, self.head[:7], {"baseline": self.head[:8]})
        self.assertEqual(m["reference"]["head"], self.head)
        self.assertEqual(m["targets"]["baseline"], self.head)

    def test_verify_passes_on_an_unchanged_tree(self):
        rc.verify_manifest(self.manifest, self.repo)

    def test_verify_fails_when_a_path_is_added(self):
        write(self.repo, "extra.txt", "x\n")
        with self.assertRaisesRegex(rc.AuditError, r"extra\.txt"):
            rc.verify_manifest(self.manifest, self.repo)

    def test_verify_fails_when_a_path_disappears(self):
        git(self.repo, "checkout", "--", "a.txt")
        with self.assertRaisesRegex(rc.AuditError, r"a\.txt"):
            rc.verify_manifest(self.manifest, self.repo)

    def test_verify_fails_when_file_bytes_change(self):
        for rel in ("a.txt", "sub/new.txt"):
            with self.subTest(path=rel):
                original = (self.repo / rel).read_bytes()
                (self.repo / rel).write_bytes(original + b"more\n")
                try:
                    with self.assertRaisesRegex(rc.AuditError, rf"{rel.replace('.', r'[.]')}.*worktree_sha256"):
                        rc.verify_manifest(self.manifest, self.repo)
                finally:
                    (self.repo / rel).write_bytes(original)

    def test_verify_fails_when_the_reference_head_moves(self):
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "moves HEAD, tree unchanged")
        with self.assertRaisesRegex(rc.AuditError, r"HEAD"):
            rc.verify_manifest(self.manifest, self.repo)

    def test_verify_fails_on_unsupported_statuses(self):
        cases = {
            "staged modification": lambda: git(self.repo, "add", "a.txt"),
            "deleted file": lambda: (self.repo / "b.txt").unlink(),
            "renamed file": lambda: git(self.repo, "mv", "b.txt", "bb.txt"),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                git(self.repo, "reset", "-q", "--hard", "HEAD")  # back to the committed tree ...
                write(self.repo, "a.txt", lines(["one", "two", "three", "four"]))  # ... then the reference change
                write(self.repo, "sub/new.txt", lines(["fresh"]))
                change()
                with self.assertRaisesRegex(rc.AuditError, r"unsupported"):
                    rc.verify_manifest(self.manifest, self.repo)

    def test_verify_fails_on_a_tampered_manifest(self):
        edited = copy.deepcopy(self.manifest)
        edited["paths"][0]["worktree_sha256"] = "0" * 64
        with self.assertRaisesRegex(rc.AuditError, r"manifest_sha256"):
            rc.verify_manifest(edited, self.repo)

    def test_a_careful_edit_that_recomputes_the_manifest_hash_still_fails(self):
        for label, edit in {
            "file hash": lambda m: m["paths"][0].__setitem__("worktree_sha256", "0" * 64),
            "rule text": lambda m: m["rules"].__setitem__("block", m["rules"]["block"] + " (edited)"),
            "diff command": lambda m: m["diff_command"].remove("--unified=0"),
        }.items():
            with self.subTest(edit=label):
                edited = copy.deepcopy(self.manifest)
                edit(edited)
                edited["manifest_sha256"] = rc.manifest_hash(edited)
                with self.assertRaises(rc.AuditError):
                    rc.verify_manifest(edited, self.repo)

    def test_a_forged_diff_input_hash_is_caught_even_when_the_dependent_hashes_are_recomputed(self):
        forged = copy.deepcopy(self.manifest)
        forged["diff_input_sha256"] = "0" * 64
        forged["input_sha256"] = rc.input_hash(forged["reference"]["head"], forged["paths"], "0" * 64)
        forged["manifest_sha256"] = rc.manifest_hash(forged)
        with self.assertRaisesRegex(rc.AuditError, r"diff_input_sha256"):
            rc.verify_manifest(forged, self.repo)

    def test_an_input_hash_that_disagrees_with_the_recorded_inputs_is_caught(self):
        forged = copy.deepcopy(self.manifest)
        forged["input_sha256"] = "0" * 64
        forged["manifest_sha256"] = rc.manifest_hash(forged)
        with self.assertRaisesRegex(rc.AuditError, r"input_sha256"):
            rc.verify_manifest(forged, self.repo)

    def test_verify_fails_when_a_pinned_target_is_not_in_the_repository(self):
        edited = copy.deepcopy(self.manifest)
        edited["targets"]["baseline"] = "0" * 40
        edited["manifest_sha256"] = rc.manifest_hash(edited)
        with self.assertRaisesRegex(rc.AuditError, r"target baseline"):
            rc.verify_manifest(edited, self.repo)

    def test_cli_records_expected_counts_and_rejects_a_malformed_expectation(self):
        out = self.repo.parent / f"{self.repo.name}-expect.json"
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        base = ["manifest", "--reference-root", str(self.repo), "--reference-head", self.head,
                "--target", f"baseline={self.head}", "--out", str(out)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(rc.main(base + ["--expect", "baseline:landed=1,absent=2"]), 0)
            self.assertEqual(rc.load_manifest(out)["expected"], {"baseline": {"landed": 1, "absent": 2}})
            self.assertEqual(rc.main(base + ["--expect", "baseline:landed=one"]), 1)
            self.assertEqual(rc.main(base + ["--expect", "baseline:nonsense=1"]), 1)

    def test_build_rejects_binary_and_non_utf8_files(self):
        write(self.repo, "blob.bin", b"\x00\x01\x02")
        with self.assertRaisesRegex(rc.AuditError, r"blob\.bin.*binary"):
            rc.build_manifest(self.repo, self.head, {"baseline": self.head})
        (self.repo / "blob.bin").unlink()
        write(self.repo, "latin.txt", b"caf\xe9\n")
        with self.assertRaisesRegex(rc.AuditError, r"latin\.txt.*UTF-8"):
            rc.build_manifest(self.repo, self.head, {"baseline": self.head})

    def test_build_rejects_a_target_that_is_not_a_commit(self):
        tree = git(self.repo, "rev-parse", "HEAD^{tree}")
        with self.assertRaisesRegex(rc.AuditError, r"commit"):
            rc.build_manifest(self.repo, self.head, {"baseline": tree})

    def test_build_rejects_a_head_that_is_not_the_checked_out_one(self):
        other = self.commit_all("c1")
        with self.assertRaisesRegex(rc.AuditError, r"HEAD"):
            rc.build_manifest(self.repo, self.head, {"baseline": other})

    def test_cli_exit_codes(self):
        manifest_path = self.repo.parent / f"{self.repo.name}-manifest.json"
        self.addCleanup(lambda: manifest_path.unlink(missing_ok=True))
        argv = ["manifest", "--reference-root", str(self.repo), "--reference-head", self.head,
                "--target", f"baseline={self.head}", "--out", str(manifest_path)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rc.main(argv), 0)
            verify = ["verify", "--manifest", str(manifest_path), "--reference-root", str(self.repo)]
            self.assertEqual(rc.main(verify), 0)
            write(self.repo, "extra.txt", "x\n")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rc.main(verify), 1)
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["reference"]["head"], self.head)


class AnalyzeTests(RepoCase):
    """`main` = the reference base (C0), `target` = what the audited branch looks like (C1)."""

    BASE_A = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "mu"]

    def setUp(self):
        super().setUp()
        write(self.repo, "a.txt", lines(self.BASE_A))
        write(self.repo, "del.txt", lines(["keep1", "remove-me-one", "remove-me-two", "keep2", "tail"]))
        write(self.repo, "del2.txt", lines(["keepA", "already-gone-one", "already-gone-two", "keepB"]))
        write(self.repo, "tiny.txt", lines(["x", "}", "}", "y"]))
        self.head = self.commit_all("c0")

        git(self.repo, "checkout", "-q", "-b", "target")
        write(self.repo, "a.txt", lines(self.BASE_A + ["landed line 1", "landed line 2", "half present A",
                                                       "half present B", "++i;"]))
        write(self.repo, "del2.txt", lines(["keepA", "keepB"]))
        self.target = self.commit_all("c1")
        git(self.repo, "checkout", "-q", "main")

        ref = list(self.BASE_A)
        for after, added in (
            ("iota", ["++i;"]),
            ("theta", ["   "]),
            ("zeta", ["absent one", "absent two"]),
            ("gamma", ["half present A", "half present B", "half missing C", "half missing D"]),
            ("alpha", ["landed line 1", "landed line 2"]),
        ):
            at = ref.index(after) + 1
            ref[at:at] = added
        write(self.repo, "a.txt", lines(ref))
        write(self.repo, "del.txt", lines(["keep1", "keep2", "tail"]))
        write(self.repo, "del2.txt", lines(["keepA", "keepB"]))
        write(self.repo, "tiny.txt", lines(["x", "y"]))
        write(self.repo, "new.txt", lines(["brand new one", "brand new two"]))
        self.manifest = rc.build_manifest(self.repo, self.head, {"baseline": self.target})

    def blocks(self, result, path):
        return {b["header"]: b for b in result["blocks"] if b["path"] == path}

    def test_counts_and_classes(self):
        result = rc.analyze(self.manifest, self.repo, "baseline")
        self.assertEqual(result["counts"], {
            "blocks_with_added_lines": 6, "landed": 2, "partial": 1, "absent": 2, "whitespace_only": 1,
            "deletion_only": 3,
        })
        self.assertEqual(result["deletion_classes"],
                         {"gone": 1, "partly-present": 0, "still-present": 1, "trivial": 1})
        by_class = sorted((b["path"], b["class"]) for b in result["blocks"])
        self.assertEqual(by_class, [
            ("a.txt", "absent"), ("a.txt", "landed"), ("a.txt", "landed"), ("a.txt", "partial"),
            ("a.txt", "whitespace_only"),
            ("del.txt", "still-present"), ("del2.txt", "gone"),
            ("new.txt", "absent"),
            ("tiny.txt", "trivial"),
        ])

    def test_lines_that_start_with_plus_or_minus_are_read_exactly(self):
        # `+++i;` is how git prints an added `++i;`; treating it as a file header would drop the line
        result = rc.analyze(self.manifest, self.repo, "baseline")
        plus = [b for b in result["blocks"] if b["path"] == "a.txt" and b["added"] == 1 and b["class"] == "landed"]
        self.assertEqual(len(plus), 1)

    def test_a_partial_block_reports_its_ratio_and_an_unmatched_sample(self):
        result = rc.analyze(self.manifest, self.repo, "baseline")
        [partial] = [b for b in result["blocks"] if b["class"] == "partial"]
        self.assertEqual((partial["added"], partial["matched"]), (4, 2))
        self.assertEqual(partial["unmatched_sample"], ["half missing C", "half missing D"])

    def test_an_untracked_file_is_one_whole_file_block_and_a_missing_target_file_is_noted(self):
        result = rc.analyze(self.manifest, self.repo, "baseline")
        [new] = [b for b in result["blocks"] if b["path"] == "new.txt"]
        self.assertEqual((new["kind"], new["header"], new["added"], new["class"]),
                         ("untracked-file", "(whole new file)", 2, "absent"))
        self.assertTrue(new["file_missing_on_target"])

    def test_deletion_only_blocks_are_kept_out_of_the_added_line_counts(self):
        result = rc.analyze(self.manifest, self.repo, "baseline")
        deletions = [b for b in result["blocks"] if b["kind"] == "deletion-only"]
        self.assertEqual(sorted(b["path"] for b in deletions), ["del.txt", "del2.txt", "tiny.txt"])
        self.assertEqual(result["counts"]["blocks_with_added_lines"], 6)

    def test_expected_counts_are_compared_when_the_manifest_has_them(self):
        good = rc.build_manifest(self.repo, self.head, {"baseline": self.target}, expected={"baseline": {
            "blocks_with_added_lines": 6, "landed": 2, "partial": 1, "absent": 2, "whitespace_only": 1,
            "deletion_only": 3}})
        self.assertIs(rc.analyze(good, self.repo, "baseline")["matches_expected"], True)
        bad = rc.build_manifest(self.repo, self.head, {"baseline": self.target}, expected={"baseline": {"landed": 3}})
        self.assertIs(rc.analyze(bad, self.repo, "baseline")["matches_expected"], False)
        self.assertIsNone(rc.analyze(self.manifest, self.repo, "baseline")["matches_expected"])

    def test_every_expected_count_has_to_match(self):
        partly = rc.build_manifest(self.repo, self.head, {"baseline": self.target},
                                   expected={"baseline": {"landed": 2, "absent": 99}})
        self.assertIs(rc.analyze(partly, self.repo, "baseline")["matches_expected"], False)

    def test_analyze_refuses_a_reference_tree_that_no_longer_matches(self):
        write(self.repo, "new.txt", lines(["brand new one", "brand new two", "sneaked in"]))
        with self.assertRaises(rc.AuditError):
            rc.analyze(self.manifest, self.repo, "baseline")

    def test_analyze_only_reads_targets_that_are_pinned_in_the_manifest(self):
        with self.assertRaisesRegex(rc.AuditError, r"pinned"):
            rc.analyze(self.manifest, self.repo, self.head)  # a real commit, but not one of the manifest's targets

    def test_a_target_can_be_named_by_its_full_sha(self):
        self.assertEqual(rc.analyze(self.manifest, self.repo, self.target)["target"]["sha"], self.target)

    def test_the_cli_exits_non_zero_when_expected_counts_do_not_match(self):
        bad = rc.build_manifest(self.repo, self.head, {"baseline": self.target}, expected={"baseline": {"landed": 3}})
        path = self.repo.parent / f"{self.repo.name}-bad.json"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        rc.write_manifest(bad, path)
        with contextlib.redirect_stdout(io.StringIO()):
            code = rc.main(["analyze", "--manifest", str(path), "--reference-root", str(self.repo),
                            "--target", "baseline"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
