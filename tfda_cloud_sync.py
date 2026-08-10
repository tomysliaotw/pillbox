"""Compatibility command for the TFDA catalogue node.

On a deployed Raspberry Pi this command imports the preloaded archive and never
needs internet. Use ``--download`` only on a preparation machine.
"""
import argparse
from pathlib import Path

from nodes.tfda_sync_node import download_and_import, import_tfda_archive
from pillbox_config import TFDA_ARCHIVE_PATH


def sync_from_cloud(archive_path: Path | str = TFDA_ARCHIVE_PATH, *, allow_network: bool = False) -> dict:
    """Backward-compatible entry point. Offline import is the safe default."""
    if allow_network:
        return download_and_import(archive_path)
    return import_tfda_archive(archive_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import a TFDA drug catalogue into the local pillbox database.")
    parser.add_argument("--archive", type=Path, default=TFDA_ARCHIVE_PATH, help="pre-downloaded TFDA ZIP archive")
    parser.add_argument("--download", action="store_true", help="download before importing; requires internet")
    args = parser.parse_args()
    result = sync_from_cloud(args.archive, allow_network=args.download)
    print(f"Imported {result['stored_records']:,} active drugs from {result['source']}.")
