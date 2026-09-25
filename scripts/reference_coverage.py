#!/usr/bin/env python3
"""Reproducible coverage audit: which parts of an uncommitted "reference" working tree exist in a target commit?

    python scripts/reference_coverage.py manifest --reference-root R --reference-head SHA \\
        --target baseline=SHA [--target final=SHA] [--expect baseline:landed=210,partial=32,...] --out manifest.json
    python scripts/reference_coverage.py verify   --manifest manifest.json --reference-root R
    python scripts/reference_coverage.py analyze  --manifest manifest.json --reference-root R --target baseline [--out result.json]

The reference tree is somebody's local, uncommitted work, so a hash of the files alone cannot say which blocks were
extracted from what. The *input manifest* pins everything the numbers depend on: the reference HEAD, the sorted list of
changed paths with the SHA-256 of every file and of every diff, the exact diff command, the rules below and the target
commits. `verify` recomputes all of it and fails on any difference (a path added or gone, a changed byte, a moved HEAD,
an unsupported kind of change, edited rules); `analyze` refuses to run unless `verify` passes, and reads target files
only with `git show <full sha>:<path>` (no network, no moving ref).

Standard library only. Tests: `python scripts/test_reference_coverage.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = 1
SCRIPT_VERSION = "1"
SCRIPT_PATH = "scripts/reference_coverage.py"

LANDED_RATIO = 0.95
PARTIAL_RATIO = 0.5
TRIVIAL_MIN_CHARS = 4

GIT_PINS = ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotepath=false"]
DIFF_FLAGS = ["diff", "--no-ext-diff", "--no-color", "--no-renames", "--diff-algorithm=myers", "--full-index",
              "--unified=0"]

RULES = {
    "block": "One hunk of the pinned '--unified=0' diff is one block; an untracked file is one whole-file block.",
    "statuses": "Only ' M' (modified in the working tree) and '??' (untracked) are supported. Any other status "
                "(staged, deleted, renamed, copied, added, conflicted) fails verification.",
    "files": "Files must be valid UTF-8 without NUL bytes; anything else fails verification.",
    "normalisation": "Lines are compared after collapsing every run of whitespace to one space and stripping both "
                     "ends; lines that are empty after that are ignored.",
    "added_blocks": "A block with added lines is classified by the share of its (non-empty, normalised) added lines "
                    "that occur as a line anywhere in the target file: >=0.95 landed, >=0.5 partial, otherwise absent. "
                    "A file missing on the target makes every share 0. A block whose added lines are all empty after "
                    "normalisation is counted as whitespace_only.",
    "mixed_blocks": "A block that also removes lines is classified by its added lines only.",
    "deletion_only_blocks": "A block with removed lines and no added lines is not part of the added-line counts. It "
                            "is listed separately and its removed lines (ignoring those with fewer than 4 non-space "
                            "characters) are checked against the target file: >=0.95 still-present, >=0.5 "
                            "partly-present, otherwise gone; if no line is left to check it is trivial.",
    "target_access": "Target files are read with 'git show <full sha>:<path>'; nothing reads the network or a "
                     "moving ref.",
}

COUNT_KEYS = ("blocks_with_added_lines", "landed", "partial", "absent", "whitespace_only", "deletion_only")
DELETION_CLASSES = ("still-present", "partly-present", "gone", "trivial")


class AuditError(Exception):
    """The input does not match the manifest, or is outside what the rules support."""


# ---------------------------------------------------------------- helpers

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def norm(line: str) -> str:
    return " ".join(line.split())


def run_git(root, *args, pins: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", *(GIT_PINS if pins else []), "-C", str(root), *args]
    proc = subprocess.run(cmd, capture_output=True)
    if check and proc.returncode != 0:
        raise AuditError(f"git {' '.join(args)} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc


def git_text(root, *args) -> str:
    return run_git(root, *args).stdout.decode("utf-8").strip()


def resolve_commit(root, rev: str) -> str:
    proc = run_git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    sha = proc.stdout.decode("utf-8").strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise AuditError(f"{rev!r} is not a commit")
    return sha


def manifest_hash(manifest: dict) -> str:
    return sha256(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"}))


def diff_command(head: str) -> list:
    return ["git", *GIT_PINS, *DIFF_FLAGS, head, "--", "<path>"]


def input_hash(head: str, paths: list, diff_input: str) -> str:
    return sha256(canonical({"head": head, "diff_command": diff_command(head), "rules": RULES, "paths": paths,
                             "diff_input_sha256": diff_input}))


# ---------------------------------------------------------------- collecting the reference input

def list_status(root) -> list:
    """(path, code) for every changed path of the reference tree, sorted bytewise; only ' M' and '??' are allowed."""
    raw = run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    tokens = raw.split(b"\0")
    found, unsupported, i = [], [], 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token:
            continue
        code = token[:2].decode("ascii", "replace")
        try:
            path = token[3:].decode("utf-8")
        except UnicodeDecodeError:
            raise AuditError(f"a changed path is not valid UTF-8: {token[3:]!r}")
        if code[0] in "RC":
            i += 1  # renames and copies carry the original path as one extra token
        if code in (" M", "??"):
            found.append((path, code))
        else:
            unsupported.append(f"{code!r} {path}")
    if unsupported:
        raise AuditError("unsupported status entries (only ' M' and '??' are allowed): " + "; ".join(unsupported))
    return sorted(found, key=lambda entry: entry[0].encode("utf-8"))


def read_checked(root, path: str) -> bytes:
    data = (Path(root) / path).read_bytes()
    if b"\0" in data:
        raise AuditError(f"{path}: binary file (contains a NUL byte)")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise AuditError(f"{path}: not valid UTF-8")
    return data


def collect_inputs(root, head: str) -> dict:
    paths, diffs = [], {}
    for path, code in list_status(root):
        data = read_checked(root, path)
        entry = {"path": path, "status": "M" if code == " M" else "??", "worktree_sha256": sha256(data),
                 "head_blob": None, "diff_sha256": None}
        if code == " M":
            entry["head_blob"] = git_text(root, "rev-parse", f"{head}:{path}")
            diff = run_git(root, *DIFF_FLAGS, head, "--", path, pins=True).stdout
            if re.search(rb"^Binary files ", diff, re.MULTILINE):
                raise AuditError(f"{path}: git reports a binary diff")
            entry["diff_sha256"] = sha256(diff)
            diffs[path] = diff
        paths.append(entry)
    diff_input = sha256(canonical([[p["path"], p["diff_sha256"]] for p in paths if p["status"] == "M"]))
    return {"paths": paths, "diffs": diffs, "diff_input_sha256": diff_input}


# ---------------------------------------------------------------- manifest

def build_manifest(root, head: str, targets: dict, expected: dict | None = None) -> dict:
    head_sha = resolve_commit(root, head)
    actual = git_text(root, "rev-parse", "HEAD")
    if actual != head_sha:
        raise AuditError(f"the reference tree's HEAD is {actual}, not {head_sha}")
    target_shas = {name: resolve_commit(root, rev) for name, rev in sorted(targets.items())}
    inputs = collect_inputs(root, head_sha)
    manifest = {
        "schema": SCHEMA,
        "script": {"path": SCRIPT_PATH, "version": SCRIPT_VERSION, "sha256": sha256(Path(__file__).read_bytes())},
        "python": platform.python_version(),
        "git": subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip(),
        "reference": {"root": Path(root).as_posix(), "head": head_sha},
        "targets": target_shas,
        "diff_command": diff_command(head_sha),
        "rules": RULES,
        "paths": inputs["paths"],
        "diff_input_sha256": inputs["diff_input_sha256"],
        "expected": expected or {},
    }
    manifest["input_sha256"] = input_hash(head_sha, inputs["paths"], inputs["diff_input_sha256"])
    manifest["manifest_sha256"] = manifest_hash(manifest)
    return manifest


def write_manifest(manifest: dict, path) -> None:
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                          encoding="utf-8", newline="\n")


def load_manifest(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_manifest(manifest: dict, root) -> dict:
    """Recompute everything the manifest pins; raise AuditError listing every difference. Returns the fresh inputs."""
    problems = []
    if manifest.get("manifest_sha256") != manifest_hash(manifest):
        problems.append("manifest_sha256 does not match the manifest's contents (edited or corrupted)")
    if manifest.get("schema") != SCHEMA:
        problems.append(f"schema {manifest.get('schema')!r} is not {SCHEMA}")
    if manifest.get("script", {}).get("version") != SCRIPT_VERSION:
        problems.append(f"script version {manifest.get('script', {}).get('version')!r} is not {SCRIPT_VERSION!r}")
    if manifest.get("rules") != RULES:
        problems.append("the manifest's rules differ from this script's rules")
    head = manifest.get("reference", {}).get("head", "")
    if manifest.get("diff_command") != diff_command(head):
        problems.append("the manifest's diff command differs from this script's pinned diff command")
    if problems:
        raise AuditError("the manifest itself is not usable:\n  - " + "\n  - ".join(problems))

    actual = git_text(root, "rev-parse", "HEAD")
    if actual != head:
        raise AuditError(f"the reference tree's HEAD is {actual}, the manifest says {head}")
    inputs = collect_inputs(root, head)
    recorded = {p["path"]: p for p in manifest["paths"]}
    current = {p["path"]: p for p in inputs["paths"]}
    added, gone = sorted(set(current) - set(recorded)), sorted(set(recorded) - set(current))
    if added:
        problems.append("changed paths that are not in the manifest: " + ", ".join(added))
    if gone:
        problems.append("paths in the manifest that are no longer changed: " + ", ".join(gone))
    for path in sorted(set(recorded) & set(current)):
        for key in ("status", "worktree_sha256", "head_blob", "diff_sha256"):
            if recorded[path][key] != current[path][key]:
                problems.append(f"{path}: {key} differs from the manifest")
    if inputs["diff_input_sha256"] != manifest["diff_input_sha256"]:
        problems.append("diff_input_sha256 differs from the manifest")
    if manifest.get("input_sha256") != input_hash(head, manifest["paths"], manifest["diff_input_sha256"]):
        problems.append("input_sha256 is inconsistent with the manifest's inputs")
    for name, sha in manifest.get("targets", {}).items():
        try:
            if resolve_commit(root, sha) != sha:
                problems.append(f"target {name} does not resolve to {sha}")
        except AuditError as error:
            problems.append(f"target {name}: {error}")
    if problems:
        raise AuditError("the reference input does not match the manifest:\n  - " + "\n  - ".join(problems))
    return inputs


# ---------------------------------------------------------------- analysis

HUNK_HEADER = re.compile(r"^(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@)")


def parse_hunks(diff_text: str) -> list:
    """Hunks of a '--unified=0' diff. Only lines *after* the first hunk header are content, so an added `++i;`
    (printed as `+++i;`) or a removed `-- x` is never mistaken for a file header."""
    hunks, current = [], None
    for line in diff_text.split("\n"):
        match = HUNK_HEADER.match(line)
        if match:
            current = {"header": match.group(1), "added": [], "removed": []}
            hunks.append(current)
        elif current is None:
            continue
        elif line.startswith("+"):
            current["added"].append(line[1:])
        elif line.startswith("-"):
            current["removed"].append(line[1:])
        # "\ No newline at end of file" and anything else carries no content
    return hunks


def target_lines(repo, sha: str, path: str):
    """The set of normalised, non-empty lines of `path` at `sha`, or None when the file does not exist there."""
    if run_git(repo, "cat-file", "-e", f"{sha}:{path}", check=False).returncode != 0:
        return None
    text = run_git(repo, "show", f"{sha}:{path}").stdout.decode("utf-8", "replace")
    return {n for n in (norm(line) for line in text.split("\n")) if n}


def classify_added(added: list, present) -> dict:
    adds = [n for n in (norm(line) for line in added) if n]
    if not adds:
        return {"class": "whitespace_only", "added": 0, "matched": 0, "unmatched_sample": []}
    matched = sum(1 for line in adds if present is not None and line in present)
    ratio = matched / len(adds)
    klass = "landed" if ratio >= LANDED_RATIO else "partial" if ratio >= PARTIAL_RATIO else "absent"
    unmatched = [line for line in adds if present is None or line not in present]
    return {"class": klass, "added": len(adds), "matched": matched,
            "unmatched_sample": [line[:120] for line in unmatched[:2]]}


def classify_removed(removed: list, present) -> dict:
    checkable = [n for n in (norm(line) for line in removed) if len(n.replace(" ", "")) >= TRIVIAL_MIN_CHARS]
    if not checkable:
        return {"class": "trivial", "removed": 0, "still_present": 0}
    still = sum(1 for line in checkable if present is not None and line in present)
    ratio = still / len(checkable)
    klass = "still-present" if ratio >= LANDED_RATIO else "partly-present" if ratio >= PARTIAL_RATIO else "gone"
    return {"class": klass, "removed": len(checkable), "still_present": still}


def analyze(manifest: dict, root, target: str, repo=None) -> dict:
    inputs = verify_manifest(manifest, root)
    pinned = manifest["targets"]
    if target in pinned:
        name, sha = target, pinned[target]
    else:
        names = [n for n, s in pinned.items() if s == target]
        if not names:
            raise AuditError(f"target {target!r} is not pinned in the manifest (pinned: {sorted(pinned)})")
        name, sha = names[0], target
    repo = repo or root
    counts = {key: 0 for key in COUNT_KEYS}
    deletion_classes = {key: 0 for key in DELETION_CLASSES}
    blocks = []

    def add_block(path, header, kind, klass_info, present):
        block = {"id": f"{path}:{header}", "path": path, "header": header, "kind": kind,
                 "file_missing_on_target": present is None, **klass_info}
        blocks.append(block)
        if kind == "deletion-only":
            counts["deletion_only"] += 1
            deletion_classes[klass_info["class"]] += 1
        else:
            counts["blocks_with_added_lines"] += 1
            counts[klass_info["class"]] += 1

    for entry in inputs["paths"]:
        path = entry["path"]
        present = target_lines(repo, sha, path)
        if entry["status"] == "??":
            text = read_checked(root, path).decode("utf-8")
            add_block(path, "(whole new file)", "untracked-file", classify_added(text.split("\n"), present), present)
            continue
        for hunk in parse_hunks(inputs["diffs"][path].decode("utf-8")):
            if hunk["added"]:
                add_block(path, hunk["header"], "tracked-hunk", classify_added(hunk["added"], present), present)
            else:
                add_block(path, hunk["header"], "deletion-only", classify_removed(hunk["removed"], present), present)

    per_file = {}
    for block in blocks:
        per_file.setdefault(block["path"], {}).setdefault(block["class"], 0)
        per_file[block["path"]][block["class"]] += 1
    wanted = manifest.get("expected", {}).get(name)
    matches = None if not wanted else all(counts.get(key) == value for key, value in wanted.items())
    return {
        "schema": SCHEMA, "manifest_sha256": manifest["manifest_sha256"], "input_sha256": manifest["input_sha256"],
        "target": {"name": name, "sha": sha}, "counts": counts, "deletion_classes": deletion_classes,
        "expected": wanted or None, "matches_expected": matches, "per_file": per_file, "blocks": blocks,
    }


# ---------------------------------------------------------------- command line

def parse_expect(values: list) -> dict:
    expected = {}
    for value in values or []:
        name, _, body = value.partition(":")
        try:
            expected[name] = {k: int(v) for k, v in (pair.split("=") for pair in body.split(","))}
        except ValueError:
            raise AuditError(f"--expect {value!r}: use NAME:key=int,key=int")
        unknown = set(expected[name]) - set(COUNT_KEYS)
        if unknown:
            raise AuditError(f"--expect {value!r}: unknown count(s) {sorted(unknown)}")
    return expected


def print_summary(result: dict) -> None:
    c = result["counts"]
    print(f"target {result['target']['name']} = {result['target']['sha']}")
    print(f"blocks with added lines: {c['blocks_with_added_lines']}  "
          f"landed={c['landed']} partial={c['partial']} absent={c['absent']} whitespace_only={c['whitespace_only']}")
    print(f"deletion-only blocks: {c['deletion_only']}  {result['deletion_classes']}")
    if result["expected"] is not None:
        print(f"expected {result['expected']}: {'MATCH' if result['matches_expected'] else 'MISMATCH'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("manifest", help="write the input manifest")
    p.add_argument("--reference-root", required=True)
    p.add_argument("--reference-head", required=True)
    p.add_argument("--target", action="append", required=True, metavar="NAME=REV")
    p.add_argument("--expect", action="append", metavar="NAME:key=int,...")
    p.add_argument("--out", required=True)
    p = sub.add_parser("verify", help="recompute the inputs and fail on any difference")
    p.add_argument("--manifest", required=True)
    p.add_argument("--reference-root", required=True)
    p = sub.add_parser("analyze", help="verify, then classify every block against a pinned target")
    p.add_argument("--manifest", required=True)
    p.add_argument("--reference-root", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--repo", help="repository to read target files from (default: the reference root)")
    p.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "manifest":
            targets = dict(pair.split("=", 1) for pair in args.target)
            manifest = build_manifest(args.reference_root, args.reference_head, targets, parse_expect(args.expect))
            write_manifest(manifest, args.out)
            print(f"wrote {args.out}: {len(manifest['paths'])} paths, input_sha256={manifest['input_sha256']}, "
                  f"manifest_sha256={manifest['manifest_sha256']}")
            return 0
        manifest = load_manifest(args.manifest)
        if args.cmd == "verify":
            inputs = verify_manifest(manifest, args.reference_root)
            print(f"OK: {len(inputs['paths'])} paths, input_sha256={manifest['input_sha256']}")
            return 0
        result = analyze(manifest, args.reference_root, args.target, args.repo)
        if args.out:
            Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                      encoding="utf-8", newline="\n")
        print_summary(result)
        return 1 if result["matches_expected"] is False else 0
    except AuditError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
