"""Mesurer une étape en conservant ses journaux et son code de sortie."""
import os
import subprocess
import sys
import time

def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: inspecter_etape.py script.py [arguments]")
    started = time.monotonic()
    command = [sys.executable, "-u", *sys.argv[1:]]
    print("Inspection : " + " ".join(sys.argv[1:]), flush=True)
    result = subprocess.run(command, check=False)
    elapsed = time.monotonic() - started
    summary = f"{sys.argv[1]} : {elapsed:.1f} s; code {result.returncode}"
    print(summary, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
            output.write("\n- " + summary + "\n")
    raise SystemExit(result.returncode if result.returncode >= 0 else 128 - result.returncode)

if __name__ == "__main__":
    main()
