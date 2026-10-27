from __future__ import annotations

import argparse
import base64
import importlib.util
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "recover_main_task.py"
SPEC = importlib.util.spec_from_file_location("recover_main_task", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def session_bytes(messages: list[tuple[str, str]]) -> bytes:
    records = []
    for index, (role, text) in enumerate(messages, 1):
        records.append(
            json.dumps(
                {
                    "timestamp": f"2026-09-28T12:{index:02d}:00Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": role,
                        "phase": "commentary" if role == "assistant" else None,
                        "content": [{"type": "text", "text": text}],
                    },
                },
                ensure_ascii=False,
            )
        )
    return ("\n".join(records) + "\n").encode("utf-8")


def visible_messages(count: int) -> list[dict[str, object]]:
    return [
        {
            "number": index,
            "role": "user" if index % 2 else "assistant",
            "phase": None,
            "timestamp": f"2026-09-28T12:{index % 60:02d}:00Z",
            "text": f"消息 {index}",
            "attachments": [],
        }
        for index in range(1, count + 1)
    ]


class TakeoverRegressionTests(unittest.TestCase):
    def create_directory_link(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_account_home_and_output_must_be_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-home-detection-") as temp:
            account_home = Path(temp) / "account-home"
            (account_home / "sessions").mkdir(parents=True)
            (account_home / "session_index.jsonl").write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit):
                MODULE.build_parser().parse_args(
                    [
                        "handoff-out",
                        "--query",
                        "test",
                        "--source-account",
                        "Parallel-B",
                        "--target-account",
                        "Main-A",
                        "--target-home",
                        str(account_home),
                        "--summary-file",
                        str(Path(temp) / "summary.md"),
                    ]
                )
            parsed = MODULE.build_parser().parse_args(
                [
                    "handoff-out",
                    "--query",
                    "test",
                    "--source-home",
                    str(account_home),
                    "--source-account",
                    "Parallel-B",
                    "--target-account",
                    "Main-A",
                    "--target-home",
                    str(account_home),
                    "--summary-file",
                    str(Path(temp) / "summary.md"),
                    "--output",
                    str(Path(temp) / "bundle"),
                ]
            )
            self.assertEqual(parsed.source_home, str(account_home))
            self.assertEqual(parsed.output, str(Path(temp) / "bundle"))

    def test_image_only_user_message_remains_the_latest_visible_message(self) -> None:
        records = [
            {
                "timestamp": "2026-09-28T12:00:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "text", "text": "正在处理"}],
                },
            },
            {
                "timestamp": "2026-09-28T12:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}],
                },
            },
        ]
        raw = ("\n".join(json.dumps(item) for item in records) + "\n").encode("utf-8")
        result = MODULE.score_session(raw, "gre", ["gre"])
        self.assertEqual(result["message_count"], 2)
        self.assertEqual(result["latest_visible_role"], "user")
        self.assertEqual(result["latest_visible_at"], "2026-09-28T12:01:00Z")
        self.assertIn("仅附件消息", result["latest_user_message"])

    def test_history_repetition_is_bounded_and_terminal_evidence_is_stronger(self) -> None:
        query = MODULE.normalize_search_text("跨设备同步 skill 继续安装 v26")
        terms = MODULE.query_terms(query)
        repeated_short = [("user", "跨设备同步 skill 打包安装") for _ in range(8)] + [
            ("assistant", "压缩包已经检查完毕，可以使用。")
        ]
        repeated_long = [("user", "跨设备同步 skill 打包安装") for _ in range(80)] + [
            ("assistant", "压缩包已经检查完毕，可以使用。")
        ]
        short_score = MODULE.score_session(session_bytes(repeated_short), query, terms)["score"]
        long_score = MODULE.score_session(session_bytes(repeated_long), query, terms)["score"]
        self.assertEqual(short_score, long_score)

        target = "v26 覆盖安装被权限拦截，需要继续安装并完成桌面交付。"
        historical = [("assistant", target)] + [("assistant", "普通进度更新") for _ in range(20)]
        terminal = [("assistant", "普通进度更新") for _ in range(20)] + [("assistant", target)]
        historical_score = MODULE.score_session(session_bytes(historical), query, terms)["score"]
        terminal_score = MODULE.score_session(session_bytes(terminal), query, terms)["score"]
        self.assertGreater(terminal_score, historical_score)

    def test_chunk_manifest_covers_every_message_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-takeover-test-") as temp:
            bundle = Path(temp)
            messages = visible_messages(151)
            transcript = MODULE.render_transcript(messages, {})
            (bundle / "FULL_TRANSCRIPT.md").write_text(transcript, encoding="utf-8")
            (bundle / "CONVERSATION_INDEX.md").write_text(MODULE.render_index(messages), encoding="utf-8")
            (bundle / "PROJECT_SNAPSHOT.md").write_text("# snapshot\n", encoding="utf-8")
            (bundle / "RECEIVER_START.md").write_text("# start\n", encoding="utf-8")
            (bundle / "TAKEOVER_METADATA.json").write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "message_count": 151,
                        "attachment_count": 0,
                        "transcript_chunk_count": 3,
                        "attachment_security_scan": {
                            "status": "not_applicable",
                            "engine": None,
                            "scanned_attachment_count": 0,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manifest = MODULE.write_transcript_chunks(bundle, messages, {})
            MODULE.write_hashes(bundle)

            self.assertEqual(manifest["chunk_count"], 3)
            self.assertEqual(manifest["chunks"][0]["message_start"], 1)
            self.assertEqual(manifest["chunks"][-1]["message_end"], 151)
            result = MODULE.verify_bundle(bundle)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["transcript_chunk_count"], 3)
            self.assertEqual(result["attachment_security_status"], "not_applicable")

            metadata_path = bundle / "TAKEOVER_METADATA.json"
            current_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            legacy_metadata = dict(current_metadata)
            legacy_metadata["schema_version"] = 3
            legacy_metadata.pop("attachment_security_scan")
            metadata_path.write_text(json.dumps(legacy_metadata, ensure_ascii=False), encoding="utf-8")
            MODULE.write_hashes(bundle)
            legacy_result = MODULE.verify_bundle(bundle)
            self.assertEqual(legacy_result["attachment_security_status"], "legacy_unverified")
            metadata_path.write_text(json.dumps(current_metadata, ensure_ascii=False), encoding="utf-8")
            MODULE.write_hashes(bundle)

            unexpected = bundle / "unexpected.txt"
            unexpected.write_text("not listed\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.TakeoverError, "没有完整覆盖"):
                MODULE.verify_bundle(bundle)
            unexpected.unlink()

            changed_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            changed_metadata["message_count"] = 150
            metadata_path.write_text(json.dumps(changed_metadata, ensure_ascii=False), encoding="utf-8")
            MODULE.write_hashes(bundle)
            with self.assertRaisesRegex(MODULE.TakeoverError, "消息数量"):
                MODULE.verify_bundle(bundle)
            changed_metadata["message_count"] = 151
            metadata_path.write_text(json.dumps(changed_metadata, ensure_ascii=False), encoding="utf-8")
            MODULE.write_hashes(bundle)

            first_chunk = bundle / manifest["chunks"][0]["file"]
            first_chunk.write_text("# 被替换的错误分块\n", encoding="utf-8")
            MODULE.write_hashes(bundle)
            with self.assertRaisesRegex(MODULE.TakeoverError, "清单哈希不一致"):
                MODULE.verify_bundle(bundle)

            manifest_path = bundle / "READING_MANIFEST.json"
            changed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            changed_manifest["chunks"][0]["sha256"] = hashlib.sha256(first_chunk.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(changed_manifest, ensure_ascii=False), encoding="utf-8")
            MODULE.write_hashes(bundle)
            with self.assertRaisesRegex(MODULE.TakeoverError, "实际消息编号"):
                MODULE.verify_bundle(bundle)

    def test_missing_referenced_attachment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-takeover-attachment-test-") as temp:
            bundle = Path(temp)
            messages = visible_messages(1)
            messages[0]["attachments"] = [1]
            names = {1: "attachment-0001.png"}
            (bundle / "FULL_TRANSCRIPT.md").write_text(
                MODULE.render_transcript(messages, names), encoding="utf-8"
            )
            (bundle / "CONVERSATION_INDEX.md").write_text(
                MODULE.render_index(messages), encoding="utf-8"
            )
            (bundle / "PROJECT_SNAPSHOT.md").write_text("# snapshot\n", encoding="utf-8")
            (bundle / "RECEIVER_START.md").write_text("# start\n", encoding="utf-8")
            (bundle / "TAKEOVER_METADATA.json").write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "message_count": 1,
                        "attachment_count": 1,
                        "transcript_chunk_count": 1,
                        "attachment_security_scan": {
                            "status": "ok",
                            "engine": "Windows.Media.Ocr",
                            "scanned_attachment_count": 1,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            MODULE.write_transcript_chunks(bundle, messages, names)
            MODULE.write_hashes(bundle)
            with self.assertRaisesRegex(MODULE.TakeoverError, "附件集合"):
                MODULE.verify_bundle(bundle)

    def test_attachment_ocr_secret_scan_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-takeover-ocr-test-") as temp:
            image = Path(temp) / "screen.png"
            secret = "Bearer " + "A" * 24
            with mock.patch.object(
                MODULE,
                "windows_ocr_texts",
                return_value=[{"path": str(image.resolve()), "text": secret}],
            ):
                with self.assertRaisesRegex(MODULE.TakeoverError, "附件 screen.png"):
                    MODULE.scan_attachment_secrets([image])

            with mock.patch.object(
                MODULE,
                "windows_ocr_texts",
                return_value=[{"path": str(image.resolve()), "text": "ordinary screenshot"}],
            ):
                result = MODULE.scan_attachment_secrets([image])
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["scanned_attachment_count"], 1)

    def test_embedded_images_require_data_url_and_matching_signature(self) -> None:
        with self.assertRaisesRegex(MODULE.TakeoverError, "无法离线"):
            MODULE.decode_data_image("https://example.invalid/private.png")
        invalid = base64.b64encode(b"not a png").decode("ascii")
        with self.assertRaisesRegex(MODULE.TakeoverError, "字节与声明类型不匹配"):
            MODULE.decode_data_image(f"data:image/png;base64,{invalid}")
        png = b"\x89PNG\r\n\x1a\n" + b"synthetic"
        payload, extension = MODULE.decode_data_image(
            "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        )
        self.assertEqual(payload, png)
        self.assertEqual(extension, ".png")

    def test_output_requires_owned_new_directory_outside_git(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-output-boundary-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            repository = root / "repository"
            (repository / ".git").mkdir(parents=True)
            with self.assertRaisesRegex(MODULE.TakeoverError, "Git 仓库"):
                MODULE.make_output_dir(project, [], repository / "nested" / "bundle", "thread")
            self.assertFalse((repository / "nested").exists())

            output = root / "neutral" / "bundle"
            created = MODULE.make_output_dir(project, [], output, "thread")
            self.assertEqual(created, output.resolve())
            marker = MODULE.ownership_marker(created)
            self.assertTrue(marker.is_file())
            (created / "partial.txt").write_text("partial", encoding="utf-8")
            MODULE.safe_remove_new_dir(created)
            self.assertFalse(created.exists())
            self.assertFalse(marker.exists())

            locked = root / "neutral" / "locked-bundle"
            locked_marker = MODULE.ownership_marker(locked)
            locked_marker.write_bytes(MODULE.marker_payload(locked))
            with self.assertRaisesRegex(MODULE.TakeoverError, "并发所有权标记"):
                MODULE.make_output_dir(project, [], locked, "thread")
            self.assertFalse(locked.exists())
            locked_marker.unlink()

            existing = root / "neutral" / "existing-bundle"
            existing.mkdir()
            sentinel = existing / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.TakeoverError, "已存在"):
                MODULE.make_output_dir(project, [], existing, "thread")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_binary_git_diff_does_not_embed_binary_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-binary-diff-") as temp:
            project = Path(temp)
            binary = project / "artifact.bin"
            binary.write_bytes(b"\x00initial")
            for command in (
                ["git", "init"],
                ["git", "config", "user.email", "test@example.invalid"],
                ["git", "config", "user.name", "Handoff Test"],
                ["git", "add", "artifact.bin"],
                ["git", "commit", "-m", "initial"],
            ):
                subprocess.run(command, cwd=project, check=True, capture_output=True)
            secret_binary = b"\x00Bearer " + b"A" * 32
            binary.write_bytes(secret_binary)
            patch = MODULE.render_git_patch(project)
            self.assertIn("Binary files", patch)
            self.assertNotIn(base64.b64encode(secret_binary).decode("ascii"), patch)
            self.assertNotIn("Bearer", patch)

    def test_uninspectable_referenced_binary_is_not_copied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-unsupported-artifact-") as temp:
            root = Path(temp)
            source = root / "private.docx"
            source.write_bytes(b"PK synthetic office file")
            output = root / "bundle"
            output.mkdir()
            messages = [{"text": f"# Files mentioned by the user:\n## private.docx: {source}\n\n## My request:\ncontinue"}]
            manifest, images = MODULE.package_referenced_artifacts(output, messages, [])
            self.assertEqual(manifest["unsupported_count"], 1)
            self.assertEqual(manifest["copied_count"], 0)
            self.assertEqual(images, [])
            self.assertFalse((output / "referenced_artifacts").exists())

    def test_non_utf8_referenced_text_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-takeover-text-test-") as temp:
            root = Path(temp)
            source = root / "legacy.txt"
            source.write_bytes(b"\x81\x81not-utf8")
            output = root / "bundle"
            output.mkdir()
            messages = [
                {
                    "text": (
                        "# Files mentioned by the user:\n"
                        f"## legacy.txt: {source}\n\n"
                        "## My request:\ncontinue"
                    )
                }
            ]

            with self.assertRaisesRegex(MODULE.TakeoverError, "不是有效 UTF-8"):
                MODULE.package_referenced_artifacts(output, messages, [])

            self.assertFalse((output / "referenced_artifacts").exists())

    def test_compact_candidate_output_does_not_expand_nested_comparisons(self) -> None:
        item = {
            "thread_id": "target",
            "title": "Target",
            "session_path": "hidden",
            "mtime_ns": 1,
            "size": 2,
            "match_snippets": ["one", "two", "three", "four"],
            "related_candidates": [{"thread_id": str(index)} for index in range(5)],
            "near_score_candidates": [{"thread_id": str(index)} for index in range(6)],
        }
        detailed = MODULE.public_candidate(item, include_comparisons=True)
        self.assertEqual(len(detailed["match_snippets"]), 3)
        self.assertEqual(len(detailed["related_candidates"]), 3)
        self.assertEqual(len(detailed["near_score_candidates"]), 3)

        compact = MODULE.public_candidate(item, include_comparisons=False)
        self.assertNotIn("match_snippets", compact)
        self.assertNotIn("related_candidates", compact)
        self.assertNotIn("near_score_candidates", compact)
        self.assertEqual(compact["related_candidate_count"], 5)
        self.assertEqual(compact["near_score_candidate_count"], 6)

    def test_discovery_skips_unrelated_unreadable_session_but_explicit_selection_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-unreadable-session-") as temp:
            root = Path(temp)
            account_home = root / "account-home"
            sessions = account_home / "sessions" / "2026" / "10" / "11"
            sessions.mkdir(parents=True)
            project = root / "project"
            project.mkdir()
            target_thread = "01999999-1111-1111-1111-111111111111"
            unreadable_thread = "01999999-2222-2222-2222-222222222222"
            index_records = [
                {
                    "id": target_thread,
                    "thread_name": "GRE website issue",
                    "updated_at": "2026-10-11T10:00:00Z",
                },
                {
                    "id": unreadable_thread,
                    "thread_name": "Unrelated archive",
                    "updated_at": "2026-10-11T09:00:00Z",
                },
            ]
            (account_home / "session_index.jsonl").write_text(
                "\n".join(json.dumps(item) for item in index_records) + "\n",
                encoding="utf-8",
            )

            def write_session(thread_id: str, messages: list[tuple[str, str]]) -> Path:
                meta = json.dumps(
                    {
                        "timestamp": "2026-10-11T09:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": thread_id, "cwd": str(project)},
                    }
                )
                path = sessions / f"rollout-{thread_id}.jsonl"
                path.write_bytes((meta + "\n").encode("utf-8") + session_bytes(messages))
                return path

            target_session = write_session(
                target_thread,
                [("user", "Please continue the GRE website issue")],
            )
            unreadable_session = write_session(
                unreadable_thread,
                [("user", "Old unrelated work")],
            )
            original_read_stable = MODULE.read_stable

            def fake_read_stable(path: Path, attempts: int = 3) -> bytes:
                if Path(path) == unreadable_session:
                    raise MODULE.TakeoverError(
                        f"会话文件超过 512 MiB 上限: {unreadable_session}"
                    )
                return original_read_stable(path, attempts)

            diagnostics: dict[str, object] = {}
            with mock.patch.object(MODULE, "read_stable", side_effect=fake_read_stable):
                candidates = MODULE.discover_candidates(
                    account_home,
                    query="GRE website issue",
                    diagnostics=diagnostics,
                )
                self.assertEqual([item["thread_id"] for item in candidates], [target_thread])
                self.assertEqual(diagnostics["unreadable_session_count"], 1)
                self.assertEqual(
                    diagnostics["unreadable_sessions"][0]["thread_id"], unreadable_thread
                )
                with self.assertRaisesRegex(MODULE.TakeoverError, "512 MiB"):
                    MODULE.discover_candidates(
                        account_home,
                        thread_id=unreadable_thread,
                    )

            self.assertTrue(target_session.is_file())

    def test_schema_two_bundle_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-takeover-old-test-") as temp:
            bundle = Path(temp)
            for name in (
                "RECEIVER_START.md",
                "PROJECT_SNAPSHOT.md",
                "CONVERSATION_INDEX.md",
                "FULL_TRANSCRIPT.md",
            ):
                (bundle / name).write_text("ok\n", encoding="utf-8")
            (bundle / "TAKEOVER_METADATA.json").write_text(
                json.dumps({"schema_version": 2}), encoding="utf-8"
            )
            MODULE.write_hashes(bundle)
            result = MODULE.verify_bundle(bundle)
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["status"], "ok")

    def test_return_handoff_round_trip_and_project_drift_detection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="main-account-return-handoff-") as temp:
            root = Path(temp)
            account_a_home = root / "account-a-home"
            account_b_home = root / "account-b-home"
            account_a_sessions = account_a_home / "sessions" / "2026" / "10" / "07"
            account_b_sessions = account_b_home / "sessions" / "2026" / "10" / "07"
            account_a_sessions.mkdir(parents=True)
            account_b_sessions.mkdir(parents=True)
            project = root / "project"
            project.mkdir()
            tracked = project / "tracked.txt"
            tracked.write_text("initial\n", encoding="utf-8")
            for command in (
                ["git", "init"],
                ["git", "config", "user.email", "handoff-test@example.invalid"],
                ["git", "config", "user.name", "Handoff Test"],
                ["git", "add", "tracked.txt"],
                ["git", "commit", "-m", "initial"],
            ):
                subprocess.run(command, cwd=project, check=True, capture_output=True)

            account_a_thread = "01999999-aaaa-bbbb-cccc-aaaaaaaaaaaa"
            account_b_thread = "01999999-aaaa-bbbb-cccc-bbbbbbbbbbbb"
            other_thread = "01999999-aaaa-bbbb-cccc-cccccccccccc"
            other_project = root / "other-project"
            other_project.mkdir()

            def write_account_session(
                sessions: Path,
                thread_id: str,
                cwd: Path,
                messages: list[tuple[str, str]],
            ) -> None:
                meta = json.dumps(
                    {
                        "timestamp": "2026-10-07T11:59:00Z",
                        "type": "session_meta",
                        "payload": {"id": thread_id, "cwd": str(cwd)},
                    }
                )
                (sessions / f"rollout-{thread_id}.jsonl").write_bytes(
                    (meta + "\n").encode("utf-8") + session_bytes(messages)
                )

            (account_a_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": account_a_thread,
                        "thread_name": "Original handoff test",
                        "updated_at": "2026-10-07T11:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (account_b_home / "session_index.jsonl").write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        {
                            "id": account_b_thread,
                            "thread_name": "Return handoff test",
                            "updated_at": "2026-10-07T12:00:00Z",
                        },
                        {
                            "id": other_thread,
                            "thread_name": "Wrong project return test",
                            "updated_at": "2026-10-07T12:01:00Z",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            write_account_session(
                account_a_sessions,
                account_a_thread,
                project,
                [("user", "Start the original handoff test")],
            )
            write_account_session(
                account_b_sessions,
                account_b_thread,
                project,
                [
                    ("user", "Continue the return handoff test"),
                    ("assistant", "Work is implemented; prepare a return package."),
                ],
            )
            write_account_session(
                account_b_sessions,
                other_thread,
                other_project,
                [("user", "Prepare a return from the wrong project")],
            )

            parent_bundle = root / "parent-bundle"
            parent_args = argparse.Namespace(
                source_home=str(account_a_home),
                target_home=str(account_b_home),
                source_account="Main-A",
                target_account="Parallel-B",
                target_thread_id=None,
                query="Original handoff test",
                project_root=str(project),
                thread_id=account_a_thread,
                output=str(parent_bundle),
            )
            parent_result = MODULE.recover(parent_args)
            self.assertEqual(parent_result["package_kind"], "takeover")
            self.assertEqual(MODULE.verify_bundle(parent_bundle)["schema_version"], 9)

            tracked.write_text("continued work\n", encoding="utf-8")
            (project / "untracked.txt").write_text("new artifact\n", encoding="utf-8")
            summary = root / "work-state.md"
            summary.write_text(
                "# 已完成\n\nImplemented tracked and untracked work.\n\n"
                "# 未完成\n\nReceiver should review.\n\n"
                "# 下一步\n\nRun the relevant tests.\n",
                encoding="utf-8",
            )
            bundle = root / "return-bundle"
            args = argparse.Namespace(
                source_home=str(account_b_home),
                query="return handoff test",
                project_root=str(project),
                thread_id=account_b_thread,
                source_account="Parallel-B",
                target_account="Main-A",
                target_home=str(account_a_home),
                target_thread_id=account_a_thread,
                summary_file=str(summary),
                parent_bundle=str(parent_bundle),
                output=str(bundle),
            )
            result = MODULE.handoff_out(args)
            self.assertEqual(result["package_kind"], "return_handoff")
            self.assertEqual(result["handoff_hop_count"], 2)
            chain = json.loads((bundle / "HANDOFF_CHAIN.json").read_text(encoding="utf-8"))
            self.assertEqual(chain["hops"][0]["direction"], "takeover")
            self.assertEqual(chain["hops"][1]["direction"], "return")

            verified = MODULE.verify_bundle(bundle)
            self.assertEqual(verified["schema_version"], 9)
            self.assertEqual(verified["package_kind"], "return_handoff")
            self.assertEqual(verified["status"], "ok")
            self.assertFalse((bundle / "RECEIVER_TOOL.py").exists())

            chain_path = bundle / "HANDOFF_CHAIN.json"
            original_chain = chain_path.read_text(encoding="utf-8")
            tampered_chain = json.loads(original_chain)
            tampered_chain["hops"][0]["target_account"] = "Wrong-B"
            chain_path.write_text(json.dumps(tampered_chain), encoding="utf-8")
            MODULE.write_hashes(bundle)
            with self.assertRaisesRegex(MODULE.TakeoverError, "账号.*不连续"):
                MODULE.verify_bundle(bundle)
            chain_path.write_text(original_chain, encoding="utf-8")
            MODULE.write_hashes(bundle)

            tampered_chain = json.loads(original_chain)
            tampered_chain["hops"][0]["project_root"] = str(other_project)
            chain_path.write_text(json.dumps(tampered_chain), encoding="utf-8")
            MODULE.write_hashes(bundle)
            with self.assertRaisesRegex(MODULE.TakeoverError, "项目根目录"):
                MODULE.verify_bundle(bundle)
            chain_path.write_text(original_chain, encoding="utf-8")
            MODULE.write_hashes(bundle)

            received = MODULE.receive_bundle(bundle, "main-a", account_a_home)
            self.assertEqual(received["status"], "ready")
            self.assertTrue(received["project_state_match"])
            self.assertEqual(received["target_thread_id"], account_a_thread)
            self.assertEqual(received["target_account_home"], str(account_a_home.resolve()))

            standalone = subprocess.run(
                [
                    "python",
                    "-X",
                    "utf8",
                    "-B",
                    str(SCRIPT),
                    "receive",
                    "--bundle",
                    str(bundle),
                    "--account",
                    "Main-A",
                    "--account-home",
                    str(account_a_home),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(standalone.returncode, 0, standalone.stderr)
            self.assertEqual(json.loads(standalone.stdout)["status"], "ready")

            with self.assertRaisesRegex(MODULE.TakeoverError, "目标账号"):
                MODULE.receive_bundle(bundle, "Wrong-Account", account_a_home)

            with self.assertRaisesRegex(MODULE.TakeoverError, "目标账号目录"):
                MODULE.receive_bundle(bundle, "Main-A", account_b_home)

            wrong_project_args = argparse.Namespace(**vars(args))
            wrong_project_args.query = "Wrong project return test"
            wrong_project_args.project_root = str(other_project)
            wrong_project_args.thread_id = other_thread
            wrong_project_args.output = str(root / "wrong-project-return-bundle")
            with self.assertRaisesRegex(MODULE.TakeoverError, "上游包项目根目录"):
                MODULE.handoff_out(wrong_project_args)
            self.assertFalse(Path(wrong_project_args.output).exists())

            forbidden_args = argparse.Namespace(**vars(args))
            forbidden_args.output = str(account_a_home / "forbidden-return-bundle")
            with self.assertRaisesRegex(MODULE.TakeoverError, "来源或目标账号目录"):
                MODULE.handoff_out(forbidden_args)
            self.assertFalse(Path(forbidden_args.output).exists())

            tracked.write_text("changed after handoff\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.TakeoverError, "Git diff 已变化"):
                MODULE.receive_bundle(bundle, "Main-A", account_a_home)

    def test_three_account_six_direction_takeover_matrix_for_non_git_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="three-codex-relay-matrix-") as temp:
            root = Path(temp)
            project = root / "shared-project"
            project.mkdir()
            referenced = project / "brief.txt"
            referenced.write_text("shared project artifact\n", encoding="utf-8")
            (project / "work.txt").write_text("non-git worktree state\n", encoding="utf-8")

            accounts: dict[str, Path] = {}
            threads: dict[str, str] = {}
            for index, name in enumerate(("Account-A", "Account-B", "Account-C"), 1):
                home = root / f"home-{index}"
                sessions = home / "sessions" / "2026" / "10" / "07"
                sessions.mkdir(parents=True)
                thread_id = f"01999999-0000-0000-0000-00000000000{index}"
                (home / "session_index.jsonl").write_text(
                    json.dumps(
                        {
                            "id": thread_id,
                            "thread_name": f"Relay task {name}",
                            "updated_at": f"2026-10-07T12:0{index}:00Z",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                meta = json.dumps(
                    {
                        "timestamp": f"2026-10-07T12:0{index}:00Z",
                        "type": "session_meta",
                        "payload": {"id": thread_id, "cwd": str(project)},
                    }
                )
                user_text = (
                    "# Files mentioned by the user:\n\n"
                    f"## brief.txt: {referenced}\n\n"
                    "## My request:\n\nContinue this relay task."
                )
                (sessions / f"rollout-{thread_id}.jsonl").write_bytes(
                    (meta + "\n").encode("utf-8")
                    + session_bytes(
                        [
                            ("user", user_text),
                            ("assistant", "The task is unfinished and ready for relay."),
                        ]
                    )
                )
                accounts[name] = home
                threads[name] = thread_id

            directions = [
                (source, target)
                for source in accounts
                for target in accounts
                if source != target
            ]
            self.assertEqual(len(directions), 6)
            for number, (source, target) in enumerate(directions, 1):
                bundle = root / f"bundle-{number}-{source}-to-{target}"
                args = argparse.Namespace(
                    source_home=str(accounts[source]),
                    target_home=str(accounts[target]),
                    source_account=source,
                    target_account=target,
                    target_thread_id=None,
                    query=f"Relay task {source}",
                    project_root=str(project),
                    thread_id=threads[source],
                    output=str(bundle),
                )
                result = MODULE.recover(args)
                self.assertEqual(result["package_kind"], "takeover")
                self.assertEqual(result["source_account"], source)
                self.assertEqual(result["target_account"], target)
                self.assertEqual(result["referenced_artifact_count"], 1)
                verified = MODULE.verify_bundle(bundle)
                self.assertEqual(verified["schema_version"], 9)
                received = MODULE.receive_bundle(bundle, target, accounts[target])
                self.assertEqual(received["status"], "ready")
                self.assertTrue(received["project_file_manifest_match"])
                self.assertEqual(received["referenced_artifact_missing_count"], 0)

    def test_explicit_external_directory_link_is_hashed_and_reverified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="takeover-external-link-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "work.txt").write_text("episode state\n", encoding="utf-8")
            shared_skill = root / "shared-skill"
            shared_skill.mkdir()
            shared_file = shared_skill / "SKILL.md"
            shared_file.write_text("# shared skill\n", encoding="utf-8")
            skills_dir = project / "skills"
            skills_dir.mkdir()
            link = skills_dir / "shared-visual-skill"
            self.create_directory_link(link, shared_skill)

            with self.assertRaisesRegex(MODULE.TakeoverError, "链接越界"):
                MODULE.project_file_manifest(project)

            allowed = ["skills/shared-visual-skill"]
            manifest = MODULE.project_file_manifest(project, allowed)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["external_link_count"], 1)
            evidence = manifest["external_links"][0]
            self.assertEqual(evidence["path"], "skills/shared-visual-skill")
            self.assertEqual(evidence["file_count"], 1)
            self.assertRegex(evidence["tree_sha256"], r"^[0-9a-f]{64}$")

            account_a = root / "home-a"
            account_b = root / "home-b"
            for account in (account_a, account_b):
                (account / "sessions").mkdir(parents=True)
                (account / "session_index.jsonl").write_text("", encoding="utf-8")
            thread_id = "01999999-1111-2222-3333-444444444444"
            session_dir = account_a / "sessions" / "2026" / "10" / "13"
            session_dir.mkdir(parents=True)
            (account_a / "session_index.jsonl").write_text(
                json.dumps(
                    {"id": thread_id, "thread_name": "External link relay", "updated_at": "2026-10-13T00:00:00Z"}
                )
                + "\n",
                encoding="utf-8",
            )
            meta = json.dumps(
                {
                    "timestamp": "2026-10-13T00:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": thread_id, "cwd": str(project)},
                }
            )
            (session_dir / f"rollout-{thread_id}.jsonl").write_bytes(
                (meta + "\n").encode("utf-8")
                + session_bytes(
                    [("user", "Continue the external link relay."), ("assistant", "Waiting to continue.")]
                )
            )
            bundle = root / "bundle"
            args = argparse.Namespace(
                source_home=str(account_a),
                target_home=str(account_b),
                source_account="Account-A",
                target_account="Account-B",
                target_thread_id=None,
                query="External link relay",
                project_root=str(project),
                thread_id=thread_id,
                output=str(bundle),
                allow_external_link=allowed,
            )
            MODULE.recover(args)
            self.assertEqual(MODULE.verify_bundle(bundle)["status"], "ok")
            self.assertEqual(MODULE.receive_bundle(bundle, "Account-B", account_b)["status"], "ready")

            shared_file.write_text("changed shared skill\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.TakeoverError, "非 Git 项目文件快照已变化"):
                MODULE.receive_bundle(bundle, "Account-B", account_b)


if __name__ == "__main__":
    unittest.main()
