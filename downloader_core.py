from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlparse

WINDOWS_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".avif")
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class CollectionSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class WorkAsset:
    url: str
    media_type: str
    index: int


@dataclass(frozen=True)
class Work:
    work_id: str
    title: str
    kind: str
    urls: tuple[str, ...]
    assets: tuple[WorkAsset, ...] = ()


@dataclass(frozen=True)
class AssetPlan:
    work_id: str
    index: int
    url: str
    media_type: str
    base_path: Path


def safe_name(value: str, fallback: str = "untitled", limit: int = 100) -> str:
    value = WINDOWS_ILLEGAL.sub("_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = fallback
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:limit].rstrip(" .") or fallback


def _best_url(url_list: object, *, prefer_last: bool = False) -> str:
    if not isinstance(url_list, list):
        return ""
    values = [str(url) for url in url_list if isinstance(url, str) and url]
    if not values:
        return ""
    return values[-1] if prefer_last else values[0]


def _best_video_url(video: object) -> str:
    if not isinstance(video, dict):
        return ""
    for key in ("play_addr", "play_addr_h264", "play_addr_265", "download_addr"):
        value = video.get(key)
        if isinstance(value, dict):
            url = _best_url(value.get("url_list"))
            if url:
                return url
    return _best_url(video.get("url_list"))


def parse_aweme(aweme: dict) -> Work | None:
    work_id = str(aweme.get("aweme_id") or aweme.get("id") or "").strip()
    if not work_id:
        return None
    title = safe_name(str(aweme.get("desc") or work_id), fallback=work_id)

    # Images take priority. Top-level album slideshow videos and music are ignored, but
    # per-image live-photo videos are kept next to their source image.
    images = aweme.get("images")
    if isinstance(images, list) and images:
        assets: list[WorkAsset] = []
        seen_assets: set[tuple[str, str]] = set()
        for index, image in enumerate(images, 1):
            if not isinstance(image, dict):
                continue
            image_url = _best_url(image.get("url_list"), prefer_last=True)
            if image_url and ("image", image_url) not in seen_assets:
                seen_assets.add(("image", image_url))
                assets.append(WorkAsset(url=image_url, media_type="image", index=index))

            live_video_url = _best_video_url(image.get("video"))
            if live_video_url and ("video", live_video_url) not in seen_assets:
                seen_assets.add(("video", live_video_url))
                assets.append(
                    WorkAsset(url=live_video_url, media_type="video", index=index)
                )
        if assets:
            return Work(
                work_id=work_id,
                title=title,
                kind="album",
                urls=tuple(asset.url for asset in assets),
                assets=tuple(assets),
            )

    video_url = _best_video_url(aweme.get("video"))
    if video_url:
        # Video URLs usually list equivalent mirrors; one URL is sufficient.
        return Work(work_id=work_id, title=title, kind="video", urls=(video_url,))
    return None


def dedupe_works(works: Iterable[Work | None]) -> list[Work]:
    result: list[Work] = []
    seen: set[str] = set()
    for work in works:
        if work is None or work.work_id in seen:
            continue
        seen.add(work.work_id)
        result.append(work)
    return result


def build_asset_plans(work: Work, output_root: Path) -> list[AssetPlan]:
    unique = f"{safe_name(work.title, fallback=work.work_id)}_{work.work_id}"
    if work.kind == "album":
        folder = output_root / "图集" / unique
        assets = work.assets or tuple(
            WorkAsset(url=url, media_type="image", index=index)
            for index, url in enumerate(work.urls, 1)
        )
        return [
            AssetPlan(
                work_id=work.work_id,
                index=asset.index,
                url=asset.url,
                media_type=asset.media_type,
                base_path=(
                    folder / f"{asset.index:03d}"
                    if asset.media_type == "image"
                    else folder / f"{asset.index:03d}_动图.mp4"
                ),
            )
            for asset in assets
        ]
    return [
        AssetPlan(
            work_id=work.work_id,
            index=1,
            url=work.urls[0],
            media_type="video",
            base_path=output_root / "视频" / f"{unique}.mp4",
        )
    ]


def discover_existing_asset(base_path: Path, media_type: str, minimum_bytes: int) -> Path | None:
    candidates = (
        [base_path]
        if media_type == "video"
        else [base_path.with_suffix(ext) for ext in IMAGE_EXTENSIONS]
    )
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size >= minimum_bytes:
                return candidate
        except OSError:
            continue
    return None


def choose_image_extension(content_type: str, url: str) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    aliases = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heic",
        "image/avif": ".avif",
    }
    if mime in aliases:
        return aliases[mime]
    guessed = mimetypes.guess_extension(mime) if mime else None
    if guessed in IMAGE_EXTENSIONS:
        return guessed
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in IMAGE_EXTENSIONS else ".jpg"


def download_asset(
    plan: AssetPlan,
    session,
    *,
    minimum_bytes: int = 1024,
    timeout: tuple[int, int] = (15, 90),
    chunk_size: int = 1024 * 1024,
) -> Path:
    """Atomically download one image/video and return its final path."""
    existing = discover_existing_asset(plan.base_path, plan.media_type, minimum_bytes)
    if existing:
        return existing

    plan.base_path.parent.mkdir(parents=True, exist_ok=True)
    response = session.get(
        plan.url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Referer": "https://www.douyin.com/",
        },
        stream=True,
        timeout=timeout,
    )
    target: Path | None = None
    partial: Path | None = None
    try:
        response.raise_for_status()
        if plan.media_type == "image":
            extension = choose_image_extension(
                response.headers.get("Content-Type", ""), plan.url
            )
            target = plan.base_path.with_suffix(extension)
        else:
            target = plan.base_path
        partial = target.with_suffix(f"{target.suffix}.part")
        with partial.open("wb") as output:
            for chunk in response.iter_content(chunk_size):
                if chunk:
                    output.write(chunk)
        if partial.stat().st_size < minimum_bytes:
            raise ValueError(f"下载内容过小：{partial.stat().st_size} bytes")
        os.replace(partial, target)
        return target
    finally:
        try:
            response.close()
        finally:
            if partial and partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass


def select_collection(collections: Sequence[dict], query: str) -> dict:
    key = query.strip()
    if not key:
        raise CollectionSelectionError("收藏夹名称不能为空")
    exact = [item for item in collections if str(item.get("name", "")).strip() == key]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [item for item in collections if key.casefold() in str(item.get("name", "")).casefold()]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if not fuzzy:
        names = "、".join(f"「{str(item.get('name', '')).strip()}」" for item in collections)
        raise CollectionSelectionError(f"没有名称包含「{key}」的收藏夹。现有：{names}")
    names = "、".join(f"「{str(item.get('name', '')).strip()}」" for item in fuzzy)
    raise CollectionSelectionError(f"「{key}」匹配到多个收藏夹：{names}。请输入完整名称。")


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS completed_works (
                collection_id TEXT NOT NULL,
                work_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                files_json TEXT NOT NULL,
                completed_at INTEGER NOT NULL,
                PRIMARY KEY (collection_id, work_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_authors (
                collection_id TEXT NOT NULL,
                work_id TEXT NOT NULL,
                author TEXT NOT NULL,
                PRIMARY KEY (collection_id, work_id)
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def is_complete(
        self,
        collection_id: str,
        work_id: str,
        minimum_bytes: int,
        expected_files: int | None = None,
    ) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT files_json FROM completed_works WHERE collection_id = ? AND work_id = ?",
                (collection_id, work_id),
            ).fetchone()
        if not row:
            return False
        try:
            files = [Path(value) for value in json.loads(row[0])]
            if expected_files is not None and len(files) != expected_files:
                return False
            return bool(files) and all(
                path.is_file() and path.stat().st_size >= minimum_bytes for path in files
            )
        except (json.JSONDecodeError, OSError, TypeError):
            return False

    def mark_complete(
        self,
        collection_id: str,
        work_id: str,
        kind: str,
        title: str,
        files: Sequence[Path],
    ) -> None:
        payload = json.dumps([str(path.resolve()) for path in files], ensure_ascii=False)
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO completed_works
                    (collection_id, work_id, kind, title, files_json, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id, work_id) DO UPDATE SET
                    kind = excluded.kind,
                    title = excluded.title,
                    files_json = excluded.files_json,
                    completed_at = excluded.completed_at
                """,
                (collection_id, work_id, kind, title, payload, int(time.time())),
            )
            self._connection.commit()

    def completed_count(self, collection_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM completed_works WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def save_authors(self, collection_id: str, author_map: dict[str, str]) -> None:
        """Persist work_id → author_name mapping, merging across sessions."""
        with self._lock:
            self._connection.executemany(
                "INSERT OR IGNORE INTO work_authors (collection_id, work_id, author) VALUES (?, ?, ?)",
                [(collection_id, wid, author) for wid, author in author_map.items()],
            )
            self._connection.commit()

    def get_author_map(self, collection_id: str) -> dict[str, str]:
        """Return all persisted {work_id: author_name} for a collection."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT work_id, author FROM work_authors WHERE collection_id = ?",
                (collection_id,),
            ).fetchall()
        return {row[0]: row[1] for row in rows}
