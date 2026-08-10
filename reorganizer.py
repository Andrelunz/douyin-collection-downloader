from __future__ import annotations

import filecmp
import re
import shutil
from pathlib import Path
from typing import Sequence

from downloader_core import IMAGE_EXTENSIONS, StateStore, safe_name

WORK_ID_PATTERN = re.compile(r"_(\d{19})(?:[_/.]|$)")


def extract_work_id(filename: str) -> str | None:
    """Extract 19-digit work ID from a filename like 'title_7234567890123456789.mp4'."""
    match = WORK_ID_PATTERN.search(filename)
    return match.group(1) if match else None


def scan_local_files(root: Path) -> list[tuple[str, Path]]:
    """Scan video, album, and author files under root, return (work_id, path) pairs."""
    results: list[tuple[str, Path]] = []
    video_dir = root / "视频"
    album_dir = root / "图集"
    author_dir = root / "作者"

    for directory in (video_dir, album_dir):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix.lower() in {".mp4", *IMAGE_EXTENSIONS}:
                work_id = extract_work_id(entry.name)
                if work_id:
                    results.append((work_id, entry))
            elif entry.is_dir():
                # Album folder: 图集/<title>_<id>/001.jpg and optional 001_动图.mp4.
                for asset in sorted(entry.iterdir()):
                    if asset.is_file() and asset.suffix.lower() in {".mp4", *IMAGE_EXTENSIONS}:
                        results.append((extract_work_id(entry.name) or "", asset))

    # Also scan author subdirectories — files there count toward threshold
    if author_dir.is_dir():
        for author_sub in sorted(author_dir.iterdir()):
            if not author_sub.is_dir():
                continue
            for f in sorted(author_sub.iterdir()):
                if f.is_file():
                    work_id = extract_work_id(f.name)
                    if work_id:
                        results.append((work_id, f))

    return results


def reorganize_by_author(
    output_root: Path,
    work_author_map: dict[str, str],
    state: StateStore,
    collection_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Group files by author, move authors with >2 works into 作者/<author>/ folders.

    Args:
        output_root: The collection's output directory (e.g., 下载/好_xxx/).
        work_author_map: Mapping from work_id to author nickname.
        state: StateStore to update paths after moving.
        collection_id: Collection identifier for state updates.
        dry_run: If True, only report what would happen without moving.

    Returns:
        {author_name: file_count_after_move} for moved authors.
    """
    files = scan_local_files(output_root)
    if not files:
        return {}

    # Group files by author
    by_author: dict[str, list[tuple[str, Path]]] = {}
    unknown_author = "__unknown__"
    for work_id, path in files:
        nickname = work_author_map.get(work_id)
        if nickname:
            by_author.setdefault(nickname, []).append((work_id, path))
        else:
            by_author.setdefault(unknown_author, []).append((work_id, path))

    author_root = output_root / "作者"
    result: dict[str, int] = {}

    for nickname, entries in sorted(by_author.items()):
        unique_works = len({wid for wid, _ in entries})
        if nickname == unknown_author or unique_works <= 2:
            continue

        author_dir = author_root / safe_name(nickname)
        if dry_run:
            result[nickname] = unique_works
            continue

        author_dir.mkdir(parents=True, exist_ok=True)

        # Move files and update state
        moved_by_work: dict[str, list[Path]] = {}
        inferred_kind_by_work: dict[str, str] = {}
        for work_id, path in entries:
            is_album_asset = path.parent.parent.name == "图集"
            if is_album_asset:
                inferred_kind_by_work[work_id] = "album"
            else:
                inferred_kind_by_work.setdefault(work_id, "video")
            # Skip files already in author directory
            if author_dir in path.parents:
                moved_by_work.setdefault(work_id, []).append(path)
                continue
            # Determine new filename: for album assets, include parent folder name
            if is_album_asset:
                # Album asset: 图集/<title>_<id>/001.jpg → <title>_<id>_001.jpg
                #              图集/<title>_<id>/001_动图.mp4 → <title>_<id>_001_动图.mp4
                base_stem = safe_name(path.parent.name)
                new_name = f"{base_stem}_{path.stem}{path.suffix}"
            else:
                # Video: keep original filename
                new_name = path.name

            dest = author_dir / new_name
            # Remove the source only when the destination has identical content.
            if dest.exists() and dest != path:
                try:
                    if filecmp.cmp(path, dest, shallow=False):
                        path.unlink()
                        moved_by_work.setdefault(work_id, []).append(dest)
                        continue
                except OSError:
                    pass
                # Different file, same name — append counter
                counter = 1
                stem = Path(new_name).stem
                while dest.exists():
                    new_name = f"{stem}_{counter}{Path(new_name).suffix}"
                    dest = author_dir / new_name
                    counter += 1

            if dest != path:
                shutil.move(str(path), str(dest))
                moved_by_work.setdefault(work_id, []).append(dest)
            else:
                moved_by_work.setdefault(work_id, []).append(path)

        # Update state database for each affected work (always upsert, even if DB was cleaned)
        for work_id, moved_paths in moved_by_work.items():
            row = state._connection.execute(
                "SELECT kind, title FROM completed_works WHERE collection_id = ? AND work_id = ?",
                (collection_id, work_id),
            ).fetchone()
            if row:
                state.mark_complete(collection_id, work_id, row[0], row[1], moved_paths)
            else:
                # No prior record — insert so future runs won't re-download
                state.mark_complete(
                    collection_id,
                    work_id,
                    inferred_kind_by_work.get(work_id, "video"),
                    work_id,
                    moved_paths,
                )

        result[nickname] = unique_works

    # Clean up empty album folders in 图集/
    album_dir = output_root / "图集"
    if album_dir.is_dir():
        for entry in sorted(album_dir.iterdir()):
            if entry.is_dir() and not any(entry.iterdir()):
                try:
                    entry.rmdir()
                except OSError:
                    pass

    return result
