"""Read-only Git-object/current-file audit. Reports locations, never matched values.

Heuristics are review aids, not proof that a repository is free of secrets.
Ignored runtime data stays local; only its configured credential values are compared.
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private_key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "service_token": (
        rb"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{32,}|hf_[A-Za-z0-9]{25,}"
        rb"|gh[pousr]_[A-Za-z0-9]{30,})"
    ),
    "credential_uri": rb"postgres(?:ql)?(?:\+psycopg)?://[^\s:@/]+:[^\s@]+@",
    "credential_literal": (
        rb"(?i)(?:password|api_key|access_secret|bearer|workflow_token)"
        rb"\s*[=:]\s*[\"'][^\"'\r\n]+[\"']"
    ),
    "json_credential_literal": (
        rb"(?i)[\"'](?:password|api_key|access_token|session_token|refresh_token|"
        rb"workflow_token|authorization)[\"']\s*:\s*[\"'][^\"'\r\n]+[\"']"
    ),
}
FORBIDDEN_PARTS = {
    ".cache",
    ".tools",
    ".venv",
    ".uv-cache",
    "node_modules",
    ".pnpm-store",
    ".auth",
    "test-results",
    "playwright-report",
}
FORBIDDEN_SUFFIXES = (
    ".gguf",
    ".safetensors",
    ".pt",
    ".pth",
    ".bin",
    ".exe",
    ".dll",
    ".dump",
    ".backup",
    ".sql.gz",
    ".sqlite",
    ".sqlite3",
    ".zip",
)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def read_objects(root, object_ids):
    """Batch protocol avoids hundreds of process launches; bytes are never echoed."""
    result = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=("\n".join(object_ids) + "\n").encode(),
        capture_output=True,
        check=True,
    ).stdout
    content, cursor = {}, 0
    while cursor < len(result):
        end = result.index(b"\n", cursor)
        oid, _kind, size = result[cursor:end].split()
        cursor = end + 1
        length = int(size)
        content[oid.decode()] = result[cursor : cursor + length]
        cursor += length + 1
    return content


def inspect_bytes(data, secrets=()):
    findings = []
    for value in secrets:
        if value and value.encode() in data:
            findings.append({"type": "configured_secret", "line": None})
    if b"\0" in data:
        return findings
    for kind, pattern in PATTERNS.items():
        for match in re.finditer(pattern, data):
            findings.append({"type": kind, "line": data[: match.start()].count(b"\n") + 1})
    return findings


def forbidden_path(name):
    path = Path(name)
    return (
        path.name == ".env"
        or (path.name.startswith(".env.") and path.name != ".env.example")
        or bool(set(path.parts) & FORBIDDEN_PARTS)
        or name.lower().endswith(FORBIDDEN_SUFFIXES)
        or path.name.lower() in {"storage-state.json", "storagestate.json", "trace.zip"}
    )


def configured_secrets(root):
    from researchflow.core.config import Settings

    settings = Settings(_env_file=root / ".env")
    values = [settings.database_password.get_secret_value()]
    for field in ("llm_api_key", "agent_access_secret"):
        value = getattr(settings, field, None)
        if value:
            values.append(value.get_secret_value())
    for filename in (root / ".cache" / "web-e2e").glob("*credentials.json"):
        for account in json.loads(filename.read_text(encoding="utf-8")).values():
            if isinstance(account, dict) and isinstance(account.get("password"), str):
                values.append(account["password"])
    return values


def audit(root, secrets=()):
    reachable = git(root, "rev-list", "--objects", "--all").decode().splitlines()
    paths = dict(row.split(" ", 1) for row in reachable if " " in row)
    objects = (
        git(
            root,
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        )
        .decode()
        .splitlines()
    )
    content = read_objects(root, [row.split()[0] for row in objects])
    findings, forbidden, privacy = [], [], []
    locations = {}
    # rev-list assigns one display path per object. Inspect every commit tree too:
    # identical content could have appeared both as .env.example and as .env.
    for row in objects:
        commit, kind, _size = row.split()
        if kind != "commit":
            continue
        for entry in filter(None, git(root, "ls-tree", "-r", "-z", commit).split(b"\0")):
            metadata, encoded_name = entry.split(b"\t", 1)
            _mode, object_kind, oid = metadata.decode().split()
            if object_kind != "blob":
                continue
            name = encoded_name.decode("utf-8")
            locations.setdefault(oid, []).append({"commit": commit, "path": name})
            if forbidden_path(name):
                forbidden.append({"object": oid, "commit": commit, "path": name})
    blob_count = byte_count = 0
    for row in objects:
        oid, kind, size = row.split()
        if kind not in {"blob", "commit", "tag"}:
            continue
        data = content[oid]
        name = paths.get(oid, "[metadata or unreachable object]")
        if kind == "blob":
            blob_count += 1
            byte_count += int(size)
        for item in inspect_bytes(data, secrets):
            findings.append(
                {"object": oid, "path": name, "history_locations": locations.get(oid, []), **item}
            )
        if kind == "blob" and forbidden_path(name):
            forbidden.append({"object": oid, "path": name})
        if re.search(rb"[A-Z]:[\\/]|[\w.+-]+@[\w.-]+\.[a-z]{2,}", data, re.I):
            privacy.append({"object": oid, "path": name, "type": "email_or_absolute_path"})
    # Read both index objects and actual worktree bytes; neither is assumed equal to HEAD.
    index = git(root, "ls-files", "--stage", "-z").split(b"\0")
    for row in filter(None, index):
        meta, name = row.decode().split("\t", 1)
        oid = meta.split()[1]
        for item in inspect_bytes(content[oid], secrets):
            findings.append({"scope": "index", "path": name, **item})
    candidates = (
        git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
    )
    for name in filter(None, candidates):
        filename = root / name
        if not filename.is_file():
            continue
        for item in inspect_bytes(filename.read_bytes(), secrets):
            findings.append({"scope": "worktree", "path": name, **item})
        if forbidden_path(name):
            forbidden.append({"scope": "worktree", "path": name})
    return {
        "head": git(root, "rev-parse", "HEAD").decode().strip(),
        "commits": len(git(root, "rev-list", "--all").splitlines()),
        "stored_objects": len(objects),
        "blobs": blob_count,
        "blob_bytes": byte_count,
        "index_entries": len(list(filter(None, index))),
        "worktree_candidates": len(list(filter(None, candidates))),
        "tags": git(root, "tag", "--list").decode().splitlines(),
        "findings": findings,
        "forbidden_paths": forbidden,
        "privacy_locations": privacy,
        "note": "Literal candidates require contextual review; no tests-directory exemption.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".cache/release/history-audit.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # This public candidate has no Git metadata. Never discover an ancestor's
    # repository or read developer credentials just to inspect source files.
    if not (root / ".git").exists():
        names = [
            p
            for p in root.rglob("*")
            if p.is_file()
            and not any(part in FORBIDDEN_PARTS for part in p.relative_to(root).parts)
        ]
        findings = [
            {"path": p.relative_to(root).as_posix(), **item}
            for p in names
            for item in inspect_bytes(p.read_bytes())
        ]
        result = {
            "head": None,
            "commits": 0,
            "stored_objects": 0,
            "blobs": len(names),
            "blob_bytes": sum(p.stat().st_size for p in names),
            "index_entries": 0,
            "worktree_candidates": len(names),
            "tags": [],
            "findings": findings,
            "forbidden_paths": [
                p.relative_to(root).as_posix()
                for p in names
                if forbidden_path(p.relative_to(root).as_posix())
            ],
            "scope": "current source-only snapshot; no Git history or runtime secret loading",
        }
    else:
        result = audit(root, configured_secrets(root))
    output = (root / args.output).resolve()
    if not output.is_relative_to(root / ".cache"):
        raise SystemExit("Raw audit output must remain in the ignored .cache directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "commits",
                    "stored_objects",
                    "blobs",
                    "blob_bytes",
                    "index_entries",
                    "worktree_candidates",
                )
            }
        )
    )
    print(
        f"Candidate locations: {len(result['findings'])}; "
        f"forbidden paths: {len(result['forbidden_paths'])}"
    )
    print("Audit digest:", hashlib.sha256(output.read_bytes()).hexdigest())
    if any(item["type"] == "configured_secret" for item in result["findings"]):
        raise SystemExit("STOP: configured secret found; inspect redacted location report")
    if result["forbidden_paths"]:
        raise SystemExit("STOP: prohibited tracked/candidate artifacts found")


if __name__ == "__main__":
    main()
