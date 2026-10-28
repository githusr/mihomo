#!/usr/bin/env python3
"""Build the embedded Android network observer with JDK 17+ and Android SDK 36."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parent
    sdk = Path(os.environ["ANDROID_HOME"])
    android_jar = sdk / "platforms/android-36/android.jar"
    d8 = sdk / "build-tools/36.0.0/lib/d8.jar"
    for path in (android_jar, d8):
        if not path.is_file():
            raise FileNotFoundError(f"Missing Android SDK component: {path}")

    def java_tool(name):
        suffix = ".exe" if os.name == "nt" else ""
        return str(Path(os.environ["JAVA_HOME"]) / "bin" / (name + suffix)) if os.environ.get("JAVA_HOME") else name + suffix

    with tempfile.TemporaryDirectory(prefix="mihomo-system-dns-") as tmp:
        classes, dex = Path(tmp) / "classes", Path(tmp) / "dex"
        classes.mkdir()
        dex.mkdir()
        subprocess.run([java_tool("javac"), "--release", "8", "-classpath", str(android_jar),
                        "-d", str(classes), str(root / "java/SystemDnsHelper.java")], check=True)
        subprocess.run([java_tool("java"), "-cp", str(d8), "com.android.tools.r8.D8", "--release",
                        "--min-api", "26", "--lib", str(android_jar), "--output", str(dex),
                        *map(str, sorted(classes.rglob("*.class")))], check=True)
        if sorted(p.name for p in dex.iterdir()) != ["classes.dex"]:
            raise RuntimeError("System DNS helper must fit in one dex")
        shutil.copyfile(dex / "classes.dex", root / "helper.dex")


if __name__ == "__main__":
    main()
