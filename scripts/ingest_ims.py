"""Download the NASA IMS bearing dataset into data/raw/ims and check every snapshot.

    uv run python scripts/ingest_ims.py [--force]

Steps that are already done are skipped: the download if the archive's checksum matches, the
extraction if data/raw/ims exists (--force redoes it). The check always runs and exits non-zero
if the data differs from what monitor.ims expects.

Unpacking the 7z and RAR archives needs bsdtar (libarchive). Windows 10+ ships it as tar.exe and
macOS as tar; on Debian/Ubuntu install libarchive-tools.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from monitor.config import PROJECT_ROOT
from monitor.ims import EXPERIMENTS, summarize_snapshot, validate_experiment

URL = "https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip"
SHA256 = "21001ac266c465f5d345ec42d7b508c6a6328487fd9d4d7774422dd5ea10ad83"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
ARCHIVE = RAW_DIR / "ims-bearings.zip"
DATASET_DIR = RAW_DIR / "ims"
README_PDF = "Readme Document for IMS Bearing Data.pdf"
# The zip holds one 7z, which holds the readme and one RAR per experiment.
INNER_7Z = "4. Bearings/IMS.7z"
# Experiment name -> (RAR in the 7z, directory inside the RAR holding the snapshots).
# The third RAR unpacks to 4th_test/txt, a leftover of the original IMS numbering.
RARS = {
    "set1": ("1st_test.rar", "1st_test"),
    "set2": ("2nd_test.rar", "2nd_test"),
    "set3": ("3rd_test.rar", "4th_test/txt"),
}
CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download() -> None:
    if ARCHIVE.exists() and sha256_of(ARCHIVE) == SHA256:
        print(f"Archive present and verified: {ARCHIVE}")
        return
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    partial = ARCHIVE.with_suffix(".zip.part")
    # Resume an interrupted download instead of fetching 1 GB again.
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(URL, headers={"Range": f"bytes={offset}-"} if offset else {})
    try:
        _fetch(request, partial, offset)
    except urllib.error.HTTPError as exc:
        # 416: the partial file is already complete, only the rename below was missed.
        if exc.code != 416:
            raise
    actual = sha256_of(partial)
    if actual != SHA256:
        partial.unlink()
        sys.exit(f"Checksum mismatch: got {actual}, expected {SHA256}. Deleted the download.")
    partial.replace(ARCHIVE)


def _fetch(request: urllib.request.Request, partial: Path, offset: int) -> None:
    with urllib.request.urlopen(request) as response:
        if offset and response.status != 206:
            offset = 0  # Server ignored the range: start over.
        total = offset + int(response.headers["Content-Length"])
        print(f"Downloading {URL} ({total / 1e9:.2f} GB)")
        with partial.open("ab" if offset else "wb") as f:
            done, reported = offset, -1
            while chunk := response.read(CHUNK):
                f.write(chunk)
                done += len(chunk)
                if (percent := 100 * done // total) // 10 > reported:
                    reported = percent // 10
                    print(f"  {percent}%", flush=True)


def find_bsdtar() -> str:
    candidates = [shutil.which("bsdtar"), shutil.which("tar")]
    if os.name == "nt":
        # Git Bash puts GNU tar first on PATH, which cannot read 7z or RAR.
        candidates.insert(0, str(Path(os.environ["SystemRoot"]) / "System32" / "tar.exe"))
    for tar in candidates:
        if tar and Path(tar).exists():
            version = subprocess.run([tar, "--version"], capture_output=True, text=True).stdout
            if "bsdtar" in version:
                return tar
    sys.exit("bsdtar not found. On Debian/Ubuntu: apt install libarchive-tools")


def extract(force: bool) -> None:
    if DATASET_DIR.exists() and not force:
        print(f"Already extracted: {DATASET_DIR} (--force to redo)")
        return
    tar = find_bsdtar()

    def untar(archive: Path, dest: Path, *members: str) -> None:
        subprocess.run([tar, "-xf", str(archive), "-C", str(dest), *members], check=True)

    # Extract next to the target so the final move is a rename, and only a complete
    # extraction ever appears at DATASET_DIR.
    with tempfile.TemporaryDirectory(dir=RAW_DIR, prefix=".extract-") as tmp_name:
        tmp = Path(tmp_name)
        print("Unpacking the zip")
        untar(ARCHIVE, tmp, INNER_7Z)
        print("Unpacking the 7z")
        untar(tmp / INNER_7Z, tmp, README_PDF, *(rar for rar, _ in RARS.values()))
        (tmp / INNER_7Z).unlink()
        staged = tmp / "ims"
        staged.mkdir()
        (tmp / README_PDF).replace(staged / README_PDF)
        for name, (rar, inner_dir) in RARS.items():
            print(f"Unpacking {rar} into {name}")
            unpacked = tmp / f"{name}-unpacked"
            unpacked.mkdir()
            untar(tmp / rar, unpacked)
            (tmp / rar).unlink()
            (unpacked / inner_dir).replace(staged / name)
        if DATASET_DIR.exists():
            shutil.rmtree(DATASET_DIR)
        staged.replace(DATASET_DIR)


def check() -> bool:
    ok = True
    with ProcessPoolExecutor() as pool:
        for experiment in EXPERIMENTS:
            paths = sorted((DATASET_DIR / experiment.name).iterdir())
            summaries = list(pool.map(summarize_snapshot, paths, chunksize=64))
            problems = validate_experiment(experiment, summaries)
            status = "OK" if not problems else f"{len(problems)} problem(s)"
            print(f"{experiment.name}: {len(summaries)} snapshots, {status}")
            for problem in problems[:20]:
                print(f"  {problem}")
            ok = ok and not problems
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and check the NASA IMS bearing data.")
    parser.add_argument("--force", action="store_true", help="extract again even if present")
    args = parser.parse_args()
    download()
    extract(args.force)
    if not check():
        sys.exit(1)


if __name__ == "__main__":
    main()
