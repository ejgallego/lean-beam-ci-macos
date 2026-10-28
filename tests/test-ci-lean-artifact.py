#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Producer admission is metadata-only; these tests never download or execute Lean."""

import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile


spec = importlib.util.spec_from_file_location(
    "ci_lean_artifact", Path(__file__).resolve().parents[1] / "scripts/ci-lean-artifact.py")
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)


class Response(io.BytesIO):
    status = 200


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.digest = "b" * 64
        self.run = {"id": 11, "repository": {"full_name": "leanprover/lean4"},
                    "path": ".github/workflows/ci.yml", "head_sha": self.sha, "run_attempt": 2}
        self.job = {"id": 12, "run_id": 11, "run_attempt": 2, "head_sha": self.sha,
                    "status": "completed", "conclusion": "success",
                    "started_at": "2026-10-28T10:00:00Z", "completed_at": "2026-10-28T11:00:00Z",
                    "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                              for name in artifact.REQUIRED_STEPS]}
        self.payload = {"id": 13, "name": "build-Windows", "expired": False,
                        "digest": "sha256:" + self.digest, "size_in_bytes": 100,
                        "created_at": "2026-10-28T10:30:00Z",
                        "workflow_run": {"id": 11, "head_sha": self.sha}}

    def admit(self):
        return artifact.admit(self.run, self.job, self.payload, lean_sha=self.sha,
                              run_id=11, job_id=12, artifact_id=13, digest=self.digest)

    def test_completed_windows_job_is_the_gate(self):
        # Other platforms in the producer run need not determine this experimental Windows gate.
        self.run.update(status="in_progress", conclusion=None)
        self.assertEqual(self.admit()["run_attempt"], 2)
        self.run.update(status="completed", conclusion="failure")
        self.assertEqual(self.admit()["lean_sha"], self.sha)

    def test_artifact_before_tests_is_not_admission(self):
        self.job.update(status="in_progress", conclusion=None)
        with self.assertRaisesRegex(ValueError, "has not completed"):
            self.admit()

    def test_failed_windows_job_and_wrong_repository_are_rejected(self):
        self.job["conclusion"] = "failure"
        with self.assertRaisesRegex(ValueError, "has not completed"):
            self.admit()
        self.job["conclusion"] = "success"
        self.run["repository"]["full_name"] = "other/lean4"
        with self.assertRaisesRegex(ValueError, "repository"):
            self.admit()

    def test_each_required_step_must_actually_pass(self):
        for index, name in enumerate(artifact.REQUIRED_STEPS):
            for outcome in ["failure", "skipped", "cancelled"]:
                with self.subTest(step=name, outcome=outcome):
                    original = self.job["steps"][index]["conclusion"]
                    self.job["steps"][index]["conclusion"] = outcome
                    with self.assertRaisesRegex(ValueError, name):
                        self.admit()
                    self.job["steps"][index]["conclusion"] = original
        self.job["steps"].pop()
        with self.assertRaisesRegex(ValueError, "Test"):
            self.admit()

    def test_source_attempt_and_artifact_identity(self):
        cases = [("run", "id", 99), ("run", "path", "other.yml"),
                 ("run", "head_sha", "c" * 40), ("job", "id", 99),
                 ("job", "head_sha", "c" * 40), ("job", "run_id", 99),
                 ("job", "run_attempt", 1), ("payload", "id", 99),
                 ("payload", "name", "build-Linux"), ("payload", "expired", True),
                 ("payload", "digest", "sha256:" + "c" * 64),
                 ("payload", "created_at", "2026-10-28T09:00:00Z"),
                 ("payload", "created_at", "2026-10-28T12:00:00Z")]
        for target, key, value in cases:
            with self.subTest(target=target, field=key):
                record = getattr(self, target)
                original = copy.deepcopy(record[key])
                record[key] = value
                with self.assertRaises(ValueError):
                    self.admit()
                record[key] = original
        self.payload["workflow_run"]["id"] = 99
        with self.assertRaisesRegex(ValueError, "another producer"):
            self.admit()

    def test_identifiers_and_full_hashes_are_required(self):
        for value in ["0", "-1", "1; command", "1.0", ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                artifact.identifier(value)
        self.sha = "abcdef0"
        with self.assertRaisesRegex(ValueError, "full Lean"):
            self.admit()
        with self.assertRaisesRegex(ValueError, "SHA256"):
            artifact.archive_digest("short")


class TransportTest(unittest.TestCase):
    def test_default_token_archive_denial_is_explicit(self):
        client = artifact.GitHub("fixture-token")
        for status in [403, 404]:
            failure = urllib.error.HTTPError("https://api.github.com/fixture", status,
                                             "fixture rejection", {}, None)
            with mock.patch.object(client.opener, "open", side_effect=failure):
                with self.subTest(status=status), self.assertRaisesRegex(ValueError, f"HTTP {status}"):
                    client.archive_url(13)

    def test_storage_probe_does_not_forward_github_authorization(self):
        client = artifact.GitHub("fixture-token")
        redirect = urllib.error.HTTPError("https://api.github.com/fixture", 302, "redirect",
                                         {"Location": "https://storage.example/fixture?signature=opaque"}, None)
        with mock.patch.object(client.opener, "open", side_effect=redirect) as api:
            url = client.archive_url(13)
        self.assertEqual(api.call_args.args[0].get_header("Authorization"), "Bearer fixture-token")
        response = Response(b"PK fixture")
        response.status = 206
        with mock.patch.object(artifact.urllib.request, "urlopen", return_value=response) as storage:
            artifact.probe_access(url)
        self.assertIsNone(storage.call_args.args[0].get_header("Authorization"))
        self.assertEqual(storage.call_args.args[0].get_header("Range"), "bytes=0-0")

    def test_digest_failure_retires_partial_download(self):
        with tempfile.TemporaryDirectory(prefix="beam-artifact-test-") as temporary:
            destination = Path(temporary) / "artifact.zip"
            with mock.patch.object(artifact.urllib.request, "urlopen", return_value=Response(b"wrong")):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    artifact.download("https://storage.example/fixture", destination,
                                      {"artifact_size": 5, "artifact_sha256": "0" * 64})
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_exact_download_is_retained_after_size_and_hash_checks(self):
        data = b"fixture archive bytes"
        with tempfile.TemporaryDirectory(prefix="beam-artifact-test-") as temporary:
            destination = Path(temporary) / "artifact.zip"
            with mock.patch.object(artifact.urllib.request, "urlopen", return_value=Response(data)):
                artifact.download("https://storage.example/fixture", destination,
                                  {"artifact_size": len(data),
                                   "artifact_sha256": hashlib.sha256(data).hexdigest()})
            self.assertEqual(destination.read_bytes(), data)
            self.assertEqual(list(Path(temporary).iterdir()), [destination])

    def test_zip_container_admits_only_one_plain_windows_tarball(self):
        with tempfile.TemporaryDirectory(prefix="beam-artifact-test-") as temporary:
            root = Path(temporary)
            container = root / "artifact.zip"
            with zipfile.ZipFile(container, "w") as archive:
                archive.writestr("lean-99.0.0-windows.tar.zst", b"fixture archive")
            result = artifact.unpack_container(container, root)
            self.assertEqual(result["archive_sha256"], hashlib.sha256(b"fixture archive").hexdigest())
            cases = [["../lean-99.0.0-windows.tar.zst"], ["lean-linux.tar.zst"],
                     ["lean-99.0.0-windows.tar.zst", "another.tar.zst"]]
            for members in cases:
                with self.subTest(members=members):
                    with zipfile.ZipFile(container, "w") as archive:
                        for name in members:
                            archive.writestr(name, b"fixture")
                    with self.assertRaises(ValueError):
                        artifact.unpack_container(container, root)
            with zipfile.ZipFile(container, "w") as archive:
                link = zipfile.ZipInfo("lean-99.0.0-windows.tar.zst")
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, "outside")
            with self.assertRaisesRegex(ValueError, "symlink"):
                artifact.unpack_container(container, root)


if __name__ == "__main__":
    unittest.main()
