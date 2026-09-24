"""Verify the macOS clipboard adapter, restoring all original clipboard formats."""
from pathlib import Path
import json
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This desktop check requires macOS.")
    if "--worker" in sys.argv:
        from arcatom_codex.clipboard import copy_text
        sample = "ARCATOM clipboard check · 中文\nsecond line"
        started = time.perf_counter()
        if not copy_text(sample):
            raise SystemExit("System clipboard rejected the test text.")
        elapsed_ms = (time.perf_counter() - started) * 1000
        result = subprocess.run(["pbpaste"], capture_output=True, check=True, timeout=4)
        if result.stdout.decode("utf-8") != sample:
            raise SystemExit("Clipboard round trip did not match.")
        print(f"PASS: UTF-8 multiline text copied in {elapsed_ms:.1f} ms and read back.")
        return
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "--worker"])
    # AppleScript's clipboard record retains text, images and other supplied formats.
    script = f'''set originalClipboard to the clipboard as record
try
    set testResult to do shell script {json.dumps(command)}
on error errorMessage number errorNumber
    set the clipboard to originalClipboard
    error errorMessage number errorNumber
end try
set the clipboard to originalClipboard
return testResult & " Original clipboard restored."
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                            check=True, timeout=30)
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
