"""Inventory locked packages from local metadata and official package registries.

No wheels, npm tarballs or model weights are downloaded. Metadata is evidence, not legal advice.
The report deliberately distinguishes missing metadata and platform-conditional packages.
"""

import hashlib
import importlib.metadata
import json
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]


def public_json(url):
    with urlopen(
        Request(url, headers={"User-Agent": "ResearchFlow-local-license-review"}), timeout=20
    ) as response:
        return json.load(response)


def license_value(metadata):
    expression = metadata.get("license_expression") or metadata.get("License-Expression")
    classifiers = metadata.get("classifiers", [])
    if expression:
        return expression
    value = metadata.get("license") or metadata.get("License")
    if value and len(value) < 180:
        return value
    classified = [
        item.removeprefix("License :: ") for item in classifiers if item.startswith("License :: ")
    ]
    return "; ".join(classified) or "NEEDS REVIEW"


def main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    direct = {item.split("==")[0].split("[")[0] for item in project["project"]["dependencies"]}
    installed = {
        item.metadata["Name"].lower().replace("_", "-"): item
        for item in importlib.metadata.distributions()
    }

    def python_package(item):
        name, version = item["name"], item["version"]
        result = {
            "name": name,
            "version": version,
            "direct_runtime": name in direct,
            "source": f"https://pypi.org/project/{name}/{version}/",
        }
        distribution = installed.get(name)
        if distribution:
            metadata = dict(distribution.metadata)
            metadata["classifiers"] = distribution.metadata.get_all("Classifier", [])
            result["evidence"] = "installed wheel METADATA and retained license files"
            result["license_files"] = [
                {
                    "file": str(file),
                    "sha256": hashlib.sha256(
                        distribution.locate_file(file).read_bytes()
                    ).hexdigest(),
                }
                for file in distribution.files or []
                if ".dist-info/licenses/" in str(file) and distribution.locate_file(file).is_file()
            ]
        else:
            try:
                metadata = public_json(f"https://pypi.org/pypi/{name}/{version}/json")["info"]
                result["evidence"] = (
                    "official PyPI version metadata; platform-conditional, not installed locally"
                )
            except Exception as error:
                metadata = {}
                result["evidence"] = (
                    "NEEDS REVIEW: registry unavailable (" + type(error).__name__ + ")"
                )
        result["license"] = license_value(metadata)
        return result

    frontend_lock = yaml.safe_load((ROOT / "frontend/pnpm-lock.yaml").read_text(encoding="utf-8"))
    command = ["node", "scripts/license-notices.mjs", "--metadata"]
    local = {
        f"{item['name']}@{item['version']}": item
        for item in json.loads(subprocess.check_output(command, cwd=ROOT / "frontend"))
    }

    def npm_package(key):
        name, version = key.rsplit("@", 1)
        metadata = local.get(key)
        evidence = "installed package.json"
        if metadata is None:
            try:
                metadata = public_json(
                    f"https://registry.npmjs.org/{quote(name, safe='')}/{version}"
                )
                evidence = (
                    "official npm version metadata; optional platform package not installed locally"
                )
            except Exception as error:
                metadata = {}
                evidence = "NEEDS REVIEW: registry unavailable (" + type(error).__name__ + ")"
        return {
            "name": name,
            "version": version,
            "license": metadata.get("license", "NEEDS REVIEW"),
            "source": f"https://www.npmjs.com/package/{name}/v/{version}",
            "evidence": evidence,
            "direct_runtime": name in {"react", "react-dom"},
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        python = list(
            pool.map(python_package, [p for p in lock["package"] if p["name"] != "researchflow"])
        )
        npm = list(pool.map(npm_package, frontend_lock["packages"]))
    report = {
        "scope": "Complete uv.lock/pnpm-lock packages, not an OS-image SBOM or vulnerability audit",
        "python": python,
        "npm": npm,
    }
    target = ROOT / "docs/license-inventory.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Inventory: {len(python)} Python + {len(npm)} npm locked packages")
    for ecosystem in (python, npm):
        for item in ecosystem:
            if "NEEDS REVIEW" in item["license"] or "NEEDS REVIEW" in item["evidence"]:
                print("NEEDS REVIEW", item["name"], item["version"])


if __name__ == "__main__":
    main()
