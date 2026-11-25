#!/usr/bin/env python3
"""Maintain a small, patched mihomo fork using upstream's build job."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

UPSTREAM = "MetaCubeX/mihomo"
ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MARKER = "<!-- mihomo-fork:"


def run(*args, cwd=None, env=None):
    return subprocess.check_output(args, cwd=cwd or ROOT, env=env, text=True).strip()


def git(*args, cwd=None, env=None):
    return run("git", *args, cwd=cwd, env=env)


def api(path, method="GET", data=None):
    args = ["gh", "api", path, "--method", method]
    if data is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(data) if data is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def releases(repo):
    found = []
    page = 1
    while True:
        batch = api(f"repos/{repo}/releases?per_page=10&page={page}")
        found.extend(batch)
        if len(batch) < 10:
            return found
        if repo == UPSTREAM and len(stable_releases(found)) >= 2:
            return found
        page += 1


def stable_releases(items):
    return sorted((r for r in items if not r["draft"] and not r["prerelease"]
                   and TAG.fullmatch(r["tag_name"])),
                  key=lambda r: tuple(map(int, TAG.fullmatch(r["tag_name"]).groups())),
                  reverse=True)[:2]


def fingerprint(source, script, patches, repo):
    digest = hashlib.sha256()
    for value in [source.encode(), repo.encode(), script,
                  *[p.name.encode() + b"\0" + p.read_bytes() for p in patches]]:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def build_workflow(upstream, spec):
    import yaml

    job = yaml.safe_load(upstream)["jobs"]["build"]
    rows = job["strategy"]["matrix"]["jobs"]
    if not rows or len({(r["goos"], r["output"]) for r in rows}) != len(rows):
        raise ValueError("Upstream build matrix is empty or has duplicate outputs")
    variable_steps = [s for s in job["steps"] if s.get("name") == "Set variables"]
    if len(variable_steps) != 1:
        raise ValueError("Upstream Set variables step changed; update the adapter")
    variable_steps[0].clear()
    variable_steps[0].update(name="Set fork version", run="python3 .fork/fork.py env")
    checkout_steps = [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    if len(checkout_steps) != 1 or checkout_steps[0].get("with", {}).get("repository"):
        raise ValueError("Upstream checkout changed; refusing to build a different repository")
    checkout_steps[0].setdefault("with", {}).update({"persist-credentials": False})
    if "ref" in checkout_steps[0]["with"]:
        raise ValueError("Upstream checkout pins a ref instead of the generated commit")
    if any(row["goos"] == "android" for row in rows):
        build_steps = [s for s in job["steps"] if s.get("name") == "Build core"]
        if len(build_steps) != 1:
            raise ValueError("Upstream Build core step changed; update the Android helper adapter")
        job["steps"].insert(job["steps"].index(build_steps[0]), {
            "name": "Build Android system DNS helper",
            "if": "${{ matrix.jobs.goos == 'android' }}",
            "run": '"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "platforms;android-36" "build-tools;36.0.0"\n'
                   'python3 component/systemdns/test_observer.py\n'
                   'python3 component/systemdns/build.py\n',
        })
    for step in job["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact@"):
            step["with"]["if-no-files-found"] = "error"
            step["with"]["retention-days"] = 3
    if "secrets." in json.dumps(job):
        raise ValueError("Upstream build job now requires secrets; review the adapter")
    job["permissions"] = {"contents": "read"}
    job["timeout-minutes"] = 60
    job["strategy"]["fail-fast"] = False
    spec["binaries"] = [f"mihomo-{r['goos']}-{r['output']}-{spec['version']}."
                        + ("zip" if r["goos"] == "windows" else "gz") for r in rows]
    publish = {
        "needs": "build", "runs-on": "ubuntu-latest", "timeout-minutes": 45,
        "permissions": {"contents": "write", "actions": "read"},
        "steps": [
            {"uses": "actions/checkout@v6", "with": {
                "fetch-depth": 0, "ssh-key": "${{ secrets.FORK_DEPLOY_KEY }}"}},
            {"uses": "actions/download-artifact@v8", "with": {
                "path": "bin", "merge-multiple": True}},
            {"name": "Publish verified artifacts", "env": {
                "GH_TOKEN": "${{ github.token }}", "PYTHONUNBUFFERED": "1"},
             "run": "python3 .fork/fork.py publish"},
        ],
    }
    workflow = {
        "name": "Fork build",
        "run-name": f"Build {spec['tag']} [{spec['fingerprint'][:12]}]",
        "on": {"workflow_dispatch": {}},
        "concurrency": {"group": "fork-build-${{ github.ref_name }}", "cancel-in-progress": False},
        "permissions": {"contents": "read"},
        "jobs": {"build": job, "publish": publish},
    }
    return yaml.safe_dump(workflow, sort_keys=False, width=120)


def prepare(spec):
    if not SHA.fullmatch(spec["upstream_sha"]):
        raise ValueError("Invalid upstream commit")
    source = spec["upstream_sha"]
    with tempfile.TemporaryDirectory(prefix="mihomo-fork-") as tmp:
        work = Path(tmp) / "source"
        git("worktree", "add", "--detach", str(work), source)
        try:
            stamp = git("show", "-s", "--format=%ct", source)
            env = dict(os.environ, GIT_AUTHOR_NAME="mihomo fork automation",
                       GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
                       GIT_COMMITTER_NAME="mihomo fork automation",
                       GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
                       GIT_AUTHOR_DATE=f"{stamp} +0000", GIT_COMMITTER_DATE=f"{stamp} +0000")
            patches = sorted((ROOT / "patches").glob("*.patch"))
            if patches:
                git("am", "--3way", *map(str, patches), cwd=work, env=env)
            updater = work / "component/updater/update_core.go"
            old = f"https://github.com/{UPSTREAM}/releases/"
            code = updater.read_text()
            if code.count(old) != 4:
                raise ValueError("Core updater URLs changed; update the fork adapter")
            updater.write_text(code.replace(old, f"https://github.com/{spec['repo']}/releases/"))
            workflow_dir = work / ".github/workflows"
            workflow = build_workflow((workflow_dir / "build.yml").read_text(), spec)
            for path in workflow_dir.iterdir():
                if path.is_file() and path.suffix in (".yml", ".yaml"):
                    path.unlink()
            (workflow_dir / "build.yml").write_text(workflow)
            overlay = work / ".fork"
            if overlay.exists():
                raise ValueError("Upstream now uses .fork; choose another overlay directory")
            overlay.mkdir()
            shutil.copyfile(ROOT / "scripts/fork.py", overlay / "fork.py")
            (overlay / "manifest.json").write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
            git("add", "-A", cwd=work)
            git("commit", "-m", f"Apply fork patches for {spec['tag']} ({spec['fingerprint'][:12]})",
                cwd=work, env=env)
            return git("rev-parse", "HEAD", cwd=work)
        finally:
            git("worktree", "remove", "--force", str(work))


def push_ref(commit, ref):
    previous = git("ls-remote", "origin", ref).split()
    expected = previous[0] if previous else ""
    git("push", f"--force-with-lease={ref}:{expected}", "origin", f"{commit}:{ref}")


def current(release, spec):
    names = {a["name"] for a in release.get("assets", [])}
    return (not release["draft"] and f"{MARKER}{spec['fingerprint']} -->" in (release["body"] or "")
            and {"version.txt", "checksums.txt", "fork-build.json"} <= names)


def release_body(spec, commit):
    return (spec["body"] + f"\n\n---\nFork build of [{spec['upstream_sha'][:12]}]"
            f"(https://github.com/{UPSTREAM}/commit/{spec['upstream_sha']}).\n"
            f"Build: `{spec['version']}`. Source: `{commit}`.\n"
            "This fork overwrites retained releases when patches change.\n"
            f"{MARKER}{spec['fingerprint']} -->")


def make_specs(repo, upstream_releases):
    stable = stable_releases(upstream_releases)
    if not stable:
        raise ValueError("No stable upstream release found")
    alpha_sha = git("rev-parse", "refs/remotes/upstream/Alpha^{commit}")
    meta_sha = git("rev-parse", "refs/remotes/upstream/Meta^{commit}")
    items = list(reversed(stable))
    alpha = next((r for r in upstream_releases if r["tag_name"] == "Prerelease-Alpha"), None)
    if alpha is None:
        alpha = api(f"repos/{UPSTREAM}/releases/tags/Prerelease-Alpha")
    items.append(alpha)
    patches = sorted((ROOT / "patches").glob("*.patch"))
    script = (ROOT / "scripts/fork.py").read_bytes()
    specs = []
    for release in items:
        tag = release["tag_name"]
        source = alpha_sha if tag == "Prerelease-Alpha" else git("rev-parse", f"refs/upstream/tags/{tag}^{{commit}}")
        fp = fingerprint(source, script, patches, repo)
        version = f"alpha-{source[:7]}-p{fp[:12]}" if release["prerelease"] else f"{tag}-p{fp[:12]}"
        base = list(map(int, TAG.fullmatch(stable[0]["tag_name"]).groups()))
        base[-1] += 1
        package = ".".join(map(str, base)) + "." + version.replace("-", ".") if release["prerelease"] else version[1:].replace("-", ".")
        branch = "Alpha" if tag == "Prerelease-Alpha" else ("Meta" if source == meta_sha and tag == stable[0]["tag_name"] else None)
        specs.append({"repo": repo, "tag": tag, "upstream_sha": source, "fingerprint": fp,
                      "version": version, "package_version": package, "branch": branch,
                      "prerelease": release["prerelease"], "latest": tag == stable[0]["tag_name"],
                      "title": release["name"] or tag, "body": release["body"] or ""})
    if not any(s["branch"] == "Meta" for s in specs):
        fp = fingerprint(meta_sha, script, patches, repo)
        specs.append({"repo": repo, "tag": "Meta", "upstream_sha": meta_sha, "fingerprint": fp,
                      "version": f"meta-{meta_sha[:7]}-p{fp[:12]}",
                      "package_version": f"{stable[0]['tag_name'][1:]}.meta.p{fp[:12]}",
                      "branch": "Meta", "prerelease": False, "latest": False, "title": "Meta", "body": ""})
    return specs


def keepalive():
    path = ROOT / ".last-sync-activity"
    last = int(path.read_text()) if path.exists() else 0
    if time.time() - last < 30 * 86400:
        return
    path.write_text(str(int(time.time())) + "\n")
    git("add", path.name)
    git("-c", "user.name=mihomo fork automation", "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", "Keep daily synchronization active")
    git("push", "origin", "HEAD:refs/heads/automation")


def start_build(repo, spec, commit):
    branch = f"_build/{spec['tag']}"
    push_ref(commit, f"refs/heads/{branch}")
    endpoint = f"repos/{repo}/actions/workflows/build.yml/runs?event=workflow_dispatch&branch={branch}&per_page=20"
    existing = api(endpoint)["workflow_runs"]
    active = next((r for r in existing if r["head_sha"] == commit and r["status"] != "completed"), None)
    if active:
        return active
    previous = max((r["id"] for r in existing), default=0)
    api(f"repos/{repo}/actions/workflows/build.yml/dispatches", "POST", {"ref": branch})
    for _ in range(24):
        time.sleep(5)
        builds = api(endpoint)["workflow_runs"]
        started = next((r for r in builds if r["id"] > previous and r["head_sha"] == commit), None)
        if started:
            return started
    raise RuntimeError(f"Dispatch accepted but build not found: {branch}")


def prune(repo, keep):
    items = releases(repo)
    available = {r["tag_name"] for r in items if not r.get("draft", False) and MARKER in (r["body"] or "")}
    if not keep <= available:
        print("Keeping older releases until all replacement versions are available", flush=True)
        return
    for release in items:
        if release["tag_name"] not in keep and MARKER in (release["body"] or ""):
            if not TAG.fullmatch(release["tag_name"]):
                raise ValueError("Refusing to prune an unexpected managed tag")
            run("gh", "release", "delete", release["tag_name"], "--repo", repo, "--cleanup-tag", "--yes")


def sync(dry_run=False):
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        origin = git("remote", "get-url", "origin")
        match = re.fullmatch(r"(?:https://github.com/|git@github.com:)([^/]+/[^/]+)", origin)
        if not match:
            raise ValueError("origin must point to your GitHub fork")
        repo = match[1].removesuffix(".git")
    if repo.lower() == UPSTREAM.lower() or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("This automation must run in your fork")
    if "upstream" not in git("remote").splitlines():
        git("remote", "add", "upstream", f"https://github.com/{UPSTREAM}.git")
    git("fetch", "--no-tags", "upstream",
        "+refs/heads/Alpha:refs/remotes/upstream/Alpha",
        "+refs/heads/Meta:refs/remotes/upstream/Meta", "+refs/tags/*:refs/upstream/tags/*")
    specs = make_specs(repo, releases(UPSTREAM))
    existing = {r["tag_name"]: r for r in releases(repo)}
    if not dry_run:
        keepalive()
    pending, errors = [], []
    for spec in specs:
        try:
            if spec["tag"] in existing and current(existing[spec["tag"]], spec):
                print(f"Unchanged: {spec['tag']}", flush=True)
                if not dry_run:
                    tagged = git("ls-remote", "origin", f"refs/tags/{spec['tag']}").split()[0]
                    if spec["branch"]:
                        push_ref(tagged, f"refs/heads/{spec['branch']}")
                    api(f"repos/{repo}/releases/{existing[spec['tag']]['id']}", "PATCH",
                        {"name": spec["title"], "body": release_body(spec, tagged),
                         "make_latest": "true" if spec["latest"] else "false"})
                continue
            commit = prepare(spec)
            print(f"Prepared {spec['tag']}: {commit}, {len(spec['binaries'])} build targets", flush=True)
            if dry_run:
                continue
            if spec["tag"] == "Meta" and git("ls-remote", "origin", "refs/heads/Meta").startswith(commit):
                continue
            build = start_build(repo, spec, commit)
            print(build["html_url"], flush=True)
            pending.append(build)
        except Exception as exc:
            print(f"::error::{spec['tag']}: {exc}", flush=True)
            errors.append(spec["tag"])
    deadline = time.time() + 5 * 3600
    while pending and time.time() < deadline:
        remaining = []
        for build in pending:
            state = api(f"repos/{repo}/actions/runs/{build['id']}")
            if state["status"] != "completed":
                remaining.append(build)
            elif state["conclusion"] != "success":
                errors.append(state["html_url"])
                print(f"::error::Build failed: {state['html_url']}", flush=True)
            else:
                print(f"Published: {state['html_url']}", flush=True)
        pending = remaining
        if pending:
            print(f"Waiting for {len(pending)} build(s)", flush=True)
            time.sleep(45)
    if not dry_run and not pending:
        prune(repo, {s["tag"] for s in specs if s["tag"] != "Meta"})
    if pending or errors:
        raise RuntimeError(f"Some updates failed or timed out; their previous builds are retained: {errors}")


def manifest():
    spec = json.loads((ROOT / ".fork/manifest.json").read_text())
    if spec["tag"] not in ("Prerelease-Alpha", "Meta") and not TAG.fullmatch(spec["tag"]):
        raise ValueError("Unexpected release tag")
    if spec["repo"] != os.environ["GITHUB_REPOSITORY"] or spec["repo"].lower() == UPSTREAM.lower():
        raise ValueError("Repository does not match the build manifest")
    if not SHA.fullmatch(spec["upstream_sha"]):
        raise ValueError("Invalid source commit")
    return spec


def build_env():
    spec = manifest()
    values = {"VERSION": spec["version"], "PackageVersion": spec["package_version"],
              "BUILDTIME": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "CGO_ENABLED": "0", "BUILDTAG": "-extldflags --static", "GOTOOLCHAIN": "local"}
    with open(os.environ["GITHUB_ENV"], "a") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError("Multiline build variable")
            stream.write(f"{key}={value}\n")


def publish():
    spec = manifest()
    repo, tag = spec["repo"], spec["tag"]
    commit = git("rev-parse", "HEAD")
    if commit != os.environ["GITHUB_SHA"]:
        raise ValueError("Publishing checkout differs from the workflow commit")
    output = ROOT / "bin"
    names = {p.name for p in output.iterdir() if p.is_file() and not p.is_symlink()}
    if not set(spec["binaries"]) <= names or (output / "version.txt").read_text().strip() != spec["version"]:
        raise ValueError("Build is incomplete or has mismatched versions")
    if tag == "Meta":
        push_ref(commit, "refs/heads/Meta")
        return
    release = next((r for r in releases(repo) if r["tag_name"] == tag), None)
    if release and release.get("immutable"):
        raise ValueError("Disable immutable releases before overwriting builds")
    if release and MARKER not in (release["body"] or ""):
        raise ValueError("Refusing to overwrite a release not created by this automation")
    spec["patched_sha"] = commit
    (output / "fork-build.json").write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    artifacts = sorted(p for p in output.iterdir() if p.is_file() and p.name not in ("checksums.txt", "version.txt"))
    with (output / "checksums.txt").open("w") as checksums:
        for artifact in artifacts:
            with artifact.open("rb") as stream:
                checksums.write(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {artifact.name}\n")
    body = release_body(spec, commit)
    if release is None:
        # The deploy key can create tags containing workflow changes; GITHUB_TOKEN cannot always do so.
        push_ref(commit, f"refs/tags/{tag}")
        release = api(f"repos/{repo}/releases", "POST", {
            "tag_name": tag, "target_commitish": commit, "name": spec["title"], "body": body,
            "draft": True, "prerelease": spec["prerelease"], "make_latest": "false"})
    run("gh", "release", "upload", tag, *map(str, artifacts), str(output / "checksums.txt"),
        "--clobber", "--repo", repo)
    push_ref(commit, f"refs/tags/{tag}")
    run("gh", "release", "upload", tag, str(output / "version.txt"), "--clobber", "--repo", repo)
    if spec["branch"]:
        push_ref(commit, f"refs/heads/{spec['branch']}")
    api(f"repos/{repo}/releases/{release['id']}", "PATCH", {
        "name": spec["title"], "body": body, "draft": False, "prerelease": spec["prerelease"],
        "make_latest": "true" if spec["latest"] else "false"})
    wanted = {p.name for p in output.iterdir() if p.is_file()}
    for asset in api(f"repos/{repo}/releases/{release['id']}")["assets"]:
        if asset["name"] not in wanted:
            api(f"repos/{repo}/releases/assets/{asset['id']}", "DELETE")
    print(f"Published https://github.com/{repo}/releases/tag/{tag}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "env", "publish"))
    parser.add_argument("--dry-run", action="store_true", help="Prepare locally without remote writes")
    args = parser.parse_args()
    if args.command == "sync":
        sync(args.dry_run)
    elif args.command == "env":
        build_env()
    else:
        publish()
