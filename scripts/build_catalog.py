#!/usr/bin/env python3
"""Build the stable public catalog for the Codex multi-account repository."""

from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
NAME = "main-account-takeover"


def display_name() -> str:
    text = (SKILLS / NAME / "agents" / "openai.yaml").read_text(encoding="utf-8")
    match = re.search(r'^\s*display_name:\s*["\']?(.+?)["\']?\s*$', text, re.M)
    return match.group(1) if match else NAME


def main() -> int:
    provenance = json.loads((ROOT / "provenance.json").read_text(encoding="utf-8"))
    approved = {row["name"] for row in provenance.get("skills", [])}
    directories = {path.name for path in SKILLS.iterdir() if path.is_dir()}
    if approved != {NAME} or directories != approved:
        raise ValueError("publication set differs from provenance approval")
    payload = {
        "schema_version": 1,
        "repository": "ranveerankhira-gif/codex-multi-account-workflows",
        "skills": [
            {
                "name": NAME,
                "display_name_zh": display_name(),
                "purpose_zh": "在显式配置的本机 Codex 账号之间核对历史、附件与项目现场并安全接力",
                "release": "v0.1.0",
                "platforms": ["windows"],
                "risk_level": "high",
                "permissions": {
                    "network": False,
                    "filesystem_read": True,
                    "filesystem_write": True,
                    "credentials": True,
                    "external_state": False,
                    "cost_or_quota": False,
                },
                "dependencies": [
                    "Windows 10/11",
                    "Python 3.10+",
                    "Windows OCR（检查被引用图片中的疑似凭据）",
                    "可选：Git（核验 Git 项目现场）",
                ],
                "invocation_policy": "explicit-only",
                "license": "MIT",
                "version": "0.1.0",
                "archive_sha256": None,
            }
        ],
    }
    target = ROOT / "catalog.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} with 1 skill")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

