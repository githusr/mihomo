import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

loader = importlib.util.spec_from_file_location("fork", Path(__file__).parents[1] / "scripts/fork.py")
fork = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fork)

WORKFLOW = """jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        jobs:
          - {goos: linux, output: amd64}
          - {goos: windows, output: arm64}
    steps:
      - uses: actions/checkout@v6
      - name: Set variables
        run: echo upstream
      - name: Test
        run: go test ./...
      - name: Build core
        run: go build
      - uses: actions/upload-artifact@v7
        with: {path: 'mihomo*'}
  Docker:
    steps:
      - run: echo unwanted
"""


def spec():
    return dict(repo="example/mihomo", tag="v1.2.3", upstream_sha="a" * 40,
                fingerprint="b" * 64, version="v1.2.3-pbbbbbbbbbbbb", package_version="1.2.3.pbbbbbbbbbbbb",
                branch="Meta", prerelease=False, latest=True, title="v1.2.3", body="Upstream notes")


class ForkTests(unittest.TestCase):
    def test_retention_ignores_rebuild_order_drafts_and_alpha(self):
        items = [dict(tag_name=t, draft=False, prerelease=False) for t in ("v1.2.9", "v1.2.10", "v1.1.99")]
        items += [dict(tag_name="v9.0.0", draft=True, prerelease=False),
                  dict(tag_name="Prerelease-Alpha", draft=False, prerelease=True)]
        self.assertEqual([r["tag_name"] for r in fork.stable_releases(items)], ["v1.2.10", "v1.2.9"])

    def test_fingerprint_changes_with_patch_and_build_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "0001.patch"
            p.write_text("first")
            original = fork.fingerprint("a", b"script", [p], "user/mihomo")
            self.assertEqual(original, fork.fingerprint("a", b"script", [p], "user/mihomo"))
            p.write_text("second")
            self.assertNotEqual(original, fork.fingerprint("a", b"script", [p], "user/mihomo"))
            self.assertNotEqual(original, fork.fingerprint("a", b"changed", [], "user/mihomo"))

    def test_adapter_preserves_matrix_and_tests_without_external_publish(self):
        plan = spec()
        result = yaml.safe_load(fork.build_workflow(WORKFLOW, plan))
        self.assertEqual(set(result["jobs"]), {"build", "publish"})
        self.assertEqual(result["on"], {"workflow_dispatch": {}})
        original = yaml.safe_load(WORKFLOW)["jobs"]["build"]
        self.assertEqual(result["jobs"]["build"]["strategy"]["matrix"], original["strategy"]["matrix"])
        self.assertIn({"name": "Test", "run": "go test ./..."}, result["jobs"]["build"]["steps"])
        self.assertEqual(plan["binaries"], ["mihomo-linux-amd64-v1.2.3-pbbbbbbbbbbbb.gz",
                                          "mihomo-windows-arm64-v1.2.3-pbbbbbbbbbbbb.zip"])
        self.assertFalse(result["jobs"]["build"]["steps"][0]["with"]["persist-credentials"])

    def test_adapter_rejects_unknown_upstream_layout(self):
        with self.assertRaises(ValueError):
            fork.build_workflow(WORKFLOW.replace("Set variables", "Different version step"), spec())
        with self.assertRaises(ValueError):
            fork.build_workflow(WORKFLOW.replace("go build", "echo ${{ secrets.SOME_SECRET }}"), spec())

    def test_android_helper_precedes_core_without_changing_upstream_steps(self):
        upstream = yaml.safe_load(WORKFLOW)
        job = upstream["jobs"]["build"]
        job["strategy"]["matrix"]["jobs"].append({"goos": "android", "output": "arm64-v8"})
        # New upstream steps between toolchain setup and the build need no patch rebase.
        job["steps"].insert(3, {"name": "Verify toolchain", "run": "go version"})
        original_core = next(s for s in job["steps"] if s.get("name") == "Build core")
        result = yaml.safe_load(fork.build_workflow(yaml.safe_dump(upstream), spec()))["jobs"]["build"]
        self.assertEqual(result["strategy"]["matrix"], job["strategy"]["matrix"])
        steps = result["steps"]
        core = steps.index(original_core)
        self.assertEqual(steps[core - 1], {
            "name": "Build Android system DNS helper",
            "if": "${{ matrix.jobs.goos == 'android' }}",
            "run": '"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "platforms;android-36" "build-tools;36.0.0"\n'
                   'python3 component/systemdns/test_observer.py\n'
                   'python3 component/systemdns/build.py\n',
        })
        self.assertEqual(steps[core - 2], {"name": "Verify toolchain", "run": "go version"})
        for broken in ([], [original_core, original_core]):
            job["steps"] = [s for s in job["steps"] if s.get("name") != "Build core"] + broken
            with self.assertRaisesRegex(ValueError, "Build core"):
                fork.build_workflow(yaml.safe_dump(upstream), spec())

    def test_failed_or_incomplete_release_is_not_up_to_date(self):
        plan = spec()
        release = dict(draft=False, body=f"{fork.MARKER}{plan['fingerprint']} -->",
                       assets=[{"name": n} for n in ("version.txt", "checksums.txt", "fork-build.json")])
        self.assertTrue(fork.current(release, plan))
        release["draft"] = True
        self.assertFalse(fork.current(release, plan))
        release["draft"] = False
        release["assets"].pop()
        self.assertFalse(fork.current(release, plan))

    def test_prepare_replays_patch_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/build.yml").write_text(WORKFLOW)
            (root / ".github/workflows/external.yml").write_text("name: external")
            (root / "component/updater").mkdir(parents=True)
            (root / "component/updater/update_core.go").write_text((f"https://github.com/{fork.UPSTREAM}/releases/path\n") * 4)
            (root / "feature.txt").write_text("before\n")
            git("add", ".")
            git("commit", "-qm", "Upstream fixture")
            source = git("rev-parse", "HEAD")
            (root / "feature.txt").write_text("after\n")
            git("commit", "-qam", "Feature patch")
            exported = git("format-patch", "--stdout", "--full-index", "--binary", "-1")
            (root / "patches").mkdir()
            (root / "patches/0001-feature.patch").write_text(exported + "\n")
            (root / "scripts").mkdir()
            (root / "scripts/fork.py").write_text(Path(fork.__file__).read_text())
            plan = spec()
            plan["upstream_sha"] = source
            with patch.object(fork, "ROOT", root):
                first = fork.prepare(copy.deepcopy(plan))
                second = fork.prepare(copy.deepcopy(plan))
            self.assertEqual(first, second)
            self.assertEqual(git("show", f"{first}:feature.txt"), "after")
            self.assertEqual(git("show", f"{source}:feature.txt"), "before")
            self.assertNotIn("MetaCubeX", git("show", f"{first}:component/updater/update_core.go"))
            self.assertNotIn("external.yml", git("ls-tree", "-r", "--name-only", first))
            git("checkout", "--detach", source)
            (root / "feature.txt").write_text("conflicting upstream change\n")
            git("commit", "-qam", "Upstream conflicts with the patch")
            plan["upstream_sha"] = git("rev-parse", "HEAD")
            with patch.object(fork, "ROOT", root), self.assertRaises(subprocess.CalledProcessError):
                fork.prepare(plan)

    def test_missing_artifact_prevents_any_publish_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            plan = spec()
            plan["binaries"] = ["missing.gz"]
            with patch.object(fork, "ROOT", root), patch.object(fork, "manifest", return_value=plan), \
                 patch.object(fork, "git", return_value="c" * 40), patch.dict(os.environ, GITHUB_SHA="c" * 40), \
                 patch.object(fork, "api") as request, self.assertRaises(ValueError):
                fork.publish()
            request.assert_not_called()

    def test_prune_only_managed_obsolete_releases(self):
        items = [dict(tag_name=t, body=b) for t, b in [("v1.0.0", fork.MARKER),
                 ("v1.1.0", fork.MARKER), ("Prerelease-Alpha", fork.MARKER), ("manual", "user release")]]
        with patch.object(fork, "releases", return_value=items), patch.object(fork, "run") as command:
            fork.prune("example/mihomo", {"v1.1.0", "Prerelease-Alpha"})
        command.assert_called_once_with("gh", "release", "delete", "v1.0.0", "--repo", "example/mihomo", "--cleanup-tag", "--yes")

    def test_prune_keeps_previous_version_until_replacement_is_published(self):
        items = [dict(tag_name="v1.0.0", body=fork.MARKER, draft=False),
                 dict(tag_name="v1.1.0", body=fork.MARKER, draft=True)]
        with patch.object(fork, "releases", return_value=items), patch.object(fork, "run") as command:
            fork.prune("example/mihomo", {"v1.1.0"})
        command.assert_not_called()

    def test_publish_uploads_version_last_and_prunes_only_after_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            plan = spec()
            plan["binaries"] = ["mihomo-test.gz"]
            (root / "bin/mihomo-test.gz").write_bytes(b"test binary")
            (root / "bin/version.txt").write_text(plan["version"])
            release = dict(id=123, tag_name=plan["tag"], body=fork.MARKER, immutable=False)
            events = []
            def api(path, method="GET", data=None):
                events.append(("api", method, path))
                return {"assets": [{"id": 4, "name": "old.gz"}, {"id": 5, "name": "mihomo-test.gz"}]}
            with patch.object(fork, "ROOT", root), patch.object(fork, "manifest", return_value=plan), \
                 patch.object(fork, "git", return_value="c" * 40), patch.dict(os.environ, GITHUB_SHA="c" * 40), \
                 patch.object(fork, "releases", return_value=[release]), patch.object(fork, "api", side_effect=api), \
                 patch.object(fork, "run", side_effect=lambda *args: events.append(("run", args))), \
                 patch.object(fork, "push_ref", side_effect=lambda *args: events.append(("push", args))):
                fork.publish()
            uploads = [e for e in events if e[0] == "run"]
            self.assertNotIn(str(root / "bin/version.txt"), uploads[0][1])
            self.assertIn(str(root / "bin/version.txt"), uploads[-1][1])
            edit = events.index(("api", "PATCH", "repos/example/mihomo/releases/123"))
            delete = events.index(("api", "DELETE", "repos/example/mihomo/releases/assets/4"))
            self.assertLess(edit, delete)
            self.assertIn("mihomo-test.gz", (root / "bin/checksums.txt").read_text())

    def test_first_release_creates_tag_with_deploy_key_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            plan = spec()
            plan["binaries"] = ["mihomo-test.gz"]
            (root / "bin/mihomo-test.gz").write_bytes(b"test binary")
            (root / "bin/version.txt").write_text(plan["version"])
            events = []
            def api(path, method="GET", data=None):
                events.append(method)
                return {"id": 123, "assets": []}
            with patch.object(fork, "ROOT", root), patch.object(fork, "manifest", return_value=plan), \
                 patch.object(fork, "git", return_value="c" * 40), patch.dict(os.environ, GITHUB_SHA="c" * 40), \
                 patch.object(fork, "releases", return_value=[]), patch.object(fork, "api", side_effect=api), \
                 patch.object(fork, "run"), \
                 patch.object(fork, "push_ref", side_effect=lambda *args: events.append("git push")):
                fork.publish()
            self.assertLess(events.index("git push"), events.index("POST"))


if __name__ == "__main__":
    unittest.main()

