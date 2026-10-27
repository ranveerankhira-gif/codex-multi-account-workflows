from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "parallel-setup" / "Start-CodexProfile.ps1"
EXAMPLE = ROOT / "parallel-setup" / "profiles.example.json"


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher test")
class ParallelLauncherTests(unittest.TestCase):
    def run_launcher(self, profile: str, config: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(LAUNCHER),
                "-Profile",
                profile,
                "-ConfigPath",
                str(config),
                "-PlanOnly",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_example_plans_three_isolated_profiles_without_writes(self) -> None:
        roots: set[str] = set()
        homes: set[str] = set()
        data_dirs: set[str] = set()
        for profile in ("account-a", "account-b", "account-c"):
            result = self.run_launcher(profile, EXAMPLE)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout.lstrip("\ufeff"))
            self.assertEqual(plan["profile"], profile)
            self.assertFalse(plan["creates_directories"])
            self.assertFalse(plan["launches_official_appx"])
            roots.add(plan["profile_root"].casefold())
            homes.add(plan["codex_home"].casefold())
            data_dirs.add(plan["electron_data"].casefold())
        self.assertEqual(len(roots), 3)
        self.assertEqual(len(homes), 3)
        self.assertEqual(len(data_dirs), 3)

    def test_relative_and_overlapping_roots_are_rejected(self) -> None:
        fixtures = [
            {
                "profiles": [
                    {"name": "account-a", "root": ".\\relative-a"},
                    {"name": "account-b", "root": "D:\\Profiles\\account-b"},
                ]
            },
            {
                "profiles": [
                    {"name": "account-a", "root": "D:\\Profiles\\shared"},
                    {"name": "account-b", "root": "D:\\Profiles\\shared\\nested"},
                ]
            },
        ]
        with tempfile.TemporaryDirectory(prefix="codex-profile-launcher-test-") as raw:
            for index, payload in enumerate(fixtures):
                config = Path(raw) / f"invalid-{index}.json"
                config.write_text(json.dumps(payload), encoding="utf-8")
                result = self.run_launcher("account-a", config)
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
