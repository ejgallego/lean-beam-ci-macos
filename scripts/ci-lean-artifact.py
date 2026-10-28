#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.

"""Admit one tested Windows Lean artifact for the manual Beam experiment."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile


REPOSITORY = "leanprover/lean4"
REQUIRED_STEPS = ("Build Target Stage", "Check Windows libuv", "Test")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    require(re.fullmatch(r"[1-9][0-9]*", value) is not None, "expected a positive GitHub ID")
    return int(value)


def source_sha(value):
    require(re.fullmatch(r"[0-9a-f]{40}", value) is not None, "expected the full Lean source SHA")
    return value


def archive_digest(value):
    require(re.fullmatch(r"[0-9a-f]{64}", value) is not None, "expected an artifact SHA256")
    return value


def timestamp(value):
    require(isinstance(value, str), "missing producer timestamp")
    result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "producer timestamp has no timezone")
    return result


def admit(run, job, artifact, *, lean_sha, run_id, job_id, artifact_id, digest):
    source_sha(lean_sha)
    archive_digest(digest)
    require(run["id"] == run_id and job["id"] == job_id and artifact["id"] == artifact_id,
            "producer identifiers do not match")
    require(run["repository"]["full_name"] == REPOSITORY, "unexpected producer repository")
    require(run["path"] == ".github/workflows/ci.yml", "unexpected producer workflow")
    require(run["head_sha"] == lean_sha and job["head_sha"] == lean_sha,
            "producer source does not match the selected Lean SHA")
    require(job["run_id"] == run_id and job["run_attempt"] == run["run_attempt"],
            "Windows job belongs to another run or attempt")
    require(job["status"] == "completed" and job["conclusion"] == "success",
            "producer Windows job has not completed successfully")
    for name in REQUIRED_STEPS:
        steps = [step for step in job["steps"] if step["name"] == name]
        require(len(steps) == 1 and steps[0]["status"] == "completed"
                and steps[0]["conclusion"] == "success", f"producer step did not pass: {name}")
    require(artifact["name"] == "build-Windows" and artifact["expired"] is False,
            "expected the unexpired build-Windows artifact")
    origin = artifact["workflow_run"]
    require(origin["id"] == run_id and origin["head_sha"] == lean_sha,
            "artifact belongs to another producer run or source")
    require(artifact["digest"] == "sha256:" + digest, "artifact digest does not match")
    require(timestamp(job["started_at"]) <= timestamp(artifact["created_at"])
            <= timestamp(job["completed_at"]), "artifact was not created during the selected Windows job")
    require(type(artifact["size_in_bytes"]) is int and artifact["size_in_bytes"] > 0,
            "artifact size is invalid")
    return {
        "repository": REPOSITORY, "lean_sha": lean_sha, "run_id": run_id,
        "run_attempt": run["run_attempt"], "job_id": job_id, "artifact_id": artifact_id,
        "artifact_name": artifact["name"], "artifact_sha256": digest,
        "artifact_size": artifact["size_in_bytes"],
        "artifact_created_at": artifact["created_at"],
        "producer_started_at": job["started_at"], "producer_completed_at": job["completed_at"],
        "producer_steps": [{"name": name, "conclusion": "success"} for name in REQUIRED_STEPS],
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class GitHub:
    def __init__(self, token):
        require(bool(token), "GH_TOKEN is required for the artifact access probe")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, route):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}/{route}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            return self.opener.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            if error.code == 302:
                return error
            raise ValueError(f"GitHub {route} returned HTTP {error.code}; "
                             "the default workflow token may not have producer artifact access") from error

    def metadata(self, route):
        with self.request(route) as response:
            require(response.status == 200, f"unexpected metadata response for {route}")
            return json.load(response)

    def archive_url(self, artifact_id):
        with self.request(f"actions/artifacts/{artifact_id}/zip") as response:
            require(response.status == 302, "artifact API did not provide an archive redirect")
            url = response.headers.get("Location", "")
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None,
                "artifact API returned an invalid download location")
        return url


def probe_access(url):
    # The signed storage request must not receive the GitHub API Authorization header.
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            require(response.status in (200, 206) and bool(response.read(1)),
                    "artifact storage did not return archive data")
    except urllib.error.HTTPError as error:
        raise ValueError(f"artifact storage returned HTTP {error.code}; download route unavailable") from error


def download(url, destination, selection):
    partial = destination.with_suffix(".partial")
    try:
        digest = hashlib.sha256()
        size = 0
        # A fresh request deliberately carries no GitHub authorization to signed storage.
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as output:
            require(response.status == 200, "artifact storage did not return the complete archive")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                require(size <= selection["artifact_size"], "artifact exceeds the admitted size")
                digest.update(chunk)
                output.write(chunk)
        require(size == selection["artifact_size"], "downloaded artifact size does not match")
        require(digest.hexdigest() == selection["artifact_sha256"], "downloaded artifact SHA256 does not match")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def unpack_container(container, directory):
    # Do not extract arbitrary ZIP paths. Only copy the single admitted tarball to a fixed path.
    with zipfile.ZipFile(container) as archive:
        files = [entry for entry in archive.infolist() if not entry.is_dir()]
        require(len(files) == 1, "expected exactly one Windows toolchain tarball")
        entry = files[0]
        require(re.fullmatch(r"lean-[A-Za-z0-9_.+-]+-windows\.tar\.zst", entry.filename) is not None,
                "unexpected Windows toolchain archive name")
        require(not stat.S_ISLNK(entry.external_attr >> 16), "toolchain archive entry is a symlink")
        target = directory / "toolchain.tar.zst"
        with archive.open(entry) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
    with target.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"archive_name": entry.filename, "archive_path": str(target.resolve()),
            "archive_sha256": digest}


def write_report(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lean-sha", required=True, type=source_sha)
    parser.add_argument("--run-id", required=True, type=identifier)
    parser.add_argument("--job-id", required=True, type=identifier)
    parser.add_argument("--artifact-id", required=True, type=identifier)
    parser.add_argument("--sha256", required=True, type=archive_digest)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--download-dir", type=Path,
                        help="download the admitted artifact; otherwise probe access without retaining SDK bytes")
    args = parser.parse_args()
    report = {"result": "running", "lean_sha": args.lean_sha, "run_id": args.run_id,
              "job_id": args.job_id, "artifact_id": args.artifact_id}
    try:
        client = GitHub(os.environ.get("GH_TOKEN", ""))
        selection = admit(client.metadata(f"actions/runs/{args.run_id}"),
                          client.metadata(f"actions/jobs/{args.job_id}"),
                          client.metadata(f"actions/artifacts/{args.artifact_id}"),
                          lean_sha=args.lean_sha, run_id=args.run_id, job_id=args.job_id,
                          artifact_id=args.artifact_id, digest=args.sha256)
        report.update(selection)
        url = client.archive_url(args.artifact_id)
        if args.download_dir:
            args.download_dir.mkdir(parents=True, exist_ok=False)
            container = args.download_dir / "artifact.zip"
            download(url, container, selection)
            report.update(unpack_container(container, args.download_dir))
            report["result"] = "downloaded"
        else:
            probe_access(url)
            report["result"] = "admitted"
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        report.update(result="failed", error=str(error))
        try:
            write_report(args.report, report)
        except OSError as report_error:
            print(f"Could not retain artifact admission report: {report_error}", file=sys.stderr)
        parser.exit(1, f"Windows artifact admission failed: {error}\n")
    write_report(args.report, report)


if __name__ == "__main__":
    main()
