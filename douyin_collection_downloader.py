from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from douyin_api import BrowserApi, fetch_collection_works, fetch_collections
from downloader_core import (
    CollectionSelectionError,
    StateStore,
    Work,
    build_asset_plans,
    download_asset,
    safe_name,
    select_collection,
)
from reorganizer import reorganize_by_author

APP_DIR = Path(__file__).resolve().parent
PROFILE_DIR = APP_DIR / "browser_data"
OUTPUT_BASE = APP_DIR / "下载"
STATE_PATH = APP_DIR / "data" / "download_state.sqlite3"
SOURCE_PROFILE_VALUE = os.environ.get("DOUYIN_SOURCE_PROFILE")
SOURCE_PROFILE = Path(SOURCE_PROFILE_VALUE).expanduser() if SOURCE_PROFILE_VALUE else None


def create_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def print_collections(collections: list[dict]) -> None:
    if not collections:
        print("没有读取到收藏夹。请确认账号已登录且存在收藏夹。")
        return
    print("\n可用收藏夹：")
    for index, item in enumerate(collections, 1):
        count = item.get("count", 0)
        suffix = f"（页面显示 {count}）" if count else ""
        print(f"  {index}. {item['name']}{suffix}")


def prepare_profile() -> None:
    if PROFILE_DIR.exists():
        return
    if SOURCE_PROFILE and SOURCE_PROFILE.exists():
        print(f"首次运行：正在复用 {SOURCE_PROFILE} 中的抖音登录状态…")
        try:
            shutil.copytree(
                SOURCE_PROFILE,
                PROFILE_DIR,
                ignore=shutil.ignore_patterns("Singleton*", "LOCK", "lockfile"),
            )
        except OSError as error:
            print(f"无法复制旧登录状态（{error}），稍后会打开浏览器扫码登录。")
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)


def resolve_collection(collections: list[dict], requested: str | None) -> dict:
    if requested:
        return select_collection(collections, requested)
    print_collections(collections)
    while True:
        answer = input("\n请输入收藏夹序号或完整名称（直接回车退出）：").strip()
        if not answer:
            raise KeyboardInterrupt
        if answer.isdigit() and 1 <= int(answer) <= len(collections):
            return collections[int(answer) - 1]
        try:
            return select_collection(collections, answer)
        except CollectionSelectionError as error:
            print(error)


def fetch_target(collection_name: str | None, list_only: bool, page_delay: float, limit: int = 0):
    prepare_profile()
    with BrowserApi(PROFILE_DIR) as api:
        collections = fetch_collections(api)
        if list_only:
            print_collections(collections)
            return None, [], {}
        collection = resolve_collection(collections, collection_name)
        print(f"\n已选择收藏夹：{collection['name']}")

        # Ask how many works to fetch (if not specified via CLI --limit)
        if limit < 0:
            print("\n要拉取多少条？")
            print("  1. 最近 100 条")
            print("  2. 最近 200 条")
            print("  3. 最近 500 条")
            print("  4. 全部（较慢）")
            while True:
                choice = input("请输入序号 (1-4): ").strip()
                if choice == "1": limit = 100; break
                if choice == "2": limit = 200; break
                if choice == "3": limit = 500; break
                if choice == "4" or choice == "": limit = 0; break
        label = f"最近 {limit} 条" if limit else "全部"
        print(f"正在分页读取（{label}）…")

        def progress(page: int, count: int) -> None:
            print(f"  第 {page} 页，累计 {count} 个作品", end="\r", flush=True)

        works, author_map = fetch_collection_works(
            api,
            collection["id"],
            page_delay=page_delay,
            max_works=limit,
            progress=progress,
        )
        print(f"\n读取完成：{len(works)} 个作品。")
        # Persist author map so future reorganize runs have full coverage
        if author_map:
            state = StateStore(STATE_PATH)
            state.save_authors(collection["id"], author_map)
            state.close()
        return collection, works, author_map


def download_work(
    work: Work,
    collection_id: str,
    output_root: Path,
    state: StateStore,
    minimum_bytes: int,
    retries: int,
) -> tuple[str, bool, str]:
    plans = build_asset_plans(work, output_root)
    files: list[Path] = []
    session = create_session()
    try:
        for plan in plans:
            last_error: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    path = download_asset(
                        plan,
                        session,
                        minimum_bytes=minimum_bytes,
                    )
                    files.append(path)
                    break
                except Exception as error:
                    last_error = error
                    if attempt < retries:
                        time.sleep(attempt * 2)
            else:
                return work.work_id, False, str(last_error or "未知下载错误")
        state.mark_complete(
            collection_id,
            work.work_id,
            work.kind,
            work.title,
            files,
        )
        if work.kind == "album":
            image_count = sum(plan.media_type == "image" for plan in plans)
            live_video_count = sum(plan.media_type == "video" for plan in plans)
            label = f"图集 {image_count} 张"
            if live_video_count:
                label += f" + 动图 {live_video_count} 个"
        else:
            label = "视频"
        return work.work_id, True, label
    finally:
        session.close()


def run_downloads(
    collection: dict,
    works: list[Work],
    workers: int,
    minimum_bytes: int,
    retries: int,
    dry_run: bool,
    author_map: dict[str, str] | None = None,
    skip_reorg: bool = False,
) -> int:
    collection_name = safe_name(collection["name"], fallback=collection["id"])
    output_root = OUTPUT_BASE / f"{collection_name}_{collection['id']}"
    state = StateStore(STATE_PATH)
    try:
        pending = [
            work
            for work in works
            if not state.is_complete(
                collection["id"],
                work.work_id,
                minimum_bytes,
                expected_files=len(build_asset_plans(work, output_root)),
            )
        ]
        skipped = len(works) - len(pending)
        albums = sum(work.kind == "album" for work in pending)
        videos = sum(work.kind == "video" for work in pending)
        print(
            f"待处理 {len(pending)} 个：视频 {videos}，图集 {albums}；"
            f"按作品 ID 跳过已完成 {skipped} 个。"
        )
        print(f"保存目录：{output_root}")
        if dry_run:
            return 0

        if not pending:
            print("无新下载任务。")
        else:
            output_root.mkdir(parents=True, exist_ok=True)
            failures: list[tuple[str, str]] = []
            completed = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        download_work,
                        work,
                        collection["id"],
                        output_root,
                        state,
                        minimum_bytes,
                        retries,
                    ): work
                    for work in pending
                }
                for future in as_completed(futures):
                    work = futures[future]
                    try:
                        work_id, ok, message = future.result()
                    except Exception as error:
                        work_id, ok, message = work.work_id, False, str(error)
                    completed += 1
                    if ok:
                        print(f"[{completed}/{len(pending)}] ✓ {message} | {work.title}")
                    else:
                        failures.append((work_id, message))
                        print(f"[{completed}/{len(pending)}] ✗ {work.title} | {message}")

            print("\n下载结束。")
            print(f"成功：{len(pending) - len(failures)}；失败：{len(failures)}；跳过：{skipped}")
            if failures:
                failure_file = output_root / "下载失败.txt"
                failure_file.write_text(
                    "\n".join(
                        f"https://www.douyin.com/video/{work_id}\t{message}"
                        for work_id, message in failures
                    ),
                    encoding="utf-8",
                )
                print(f"失败清单：{failure_file}")
                print("重新运行同一收藏夹即可只重试未完成作品。")
                return 2
            failure_file = output_root / "下载失败.txt"
            if failure_file.exists():
                failure_file.unlink()

        # Post-download: reorganize by author
        if not skip_reorg:
            persisted_author_map = state.get_author_map(collection["id"])
            if persisted_author_map:
                print("\n[*] 按作者归类…")
                result = reorganize_by_author(
                    output_root, persisted_author_map, state, collection["id"], dry_run=dry_run
                )
                if result:
                    for author_name, count in sorted(result.items()):
                        print(f"    {author_name}: {count} 个作品 → 作者/{safe_name(author_name)}/")
                else:
                    print("    （所有作者作品数均 ≤2，无需归类）")

        return 0
    finally:
        state.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量下载指定抖音收藏夹；按作品 ID 去重；图集按作品建文件夹并支持图片自带动图。"
    )
    parser.add_argument("--collection", "--收藏夹", dest="collection", help="收藏夹完整名称（也支持唯一的部分名称）")
    parser.add_argument("--list", "--列出", action="store_true", dest="list_only", help="只列出收藏夹")
    parser.add_argument("--dry-run", action="store_true", help="只读取并统计作品，不下载")
    parser.add_argument("--workers", type=int, default=3, help="并发下载数，默认 3，范围 1-6")
    parser.add_argument("--retries", type=int, default=3, help="单个文件重试次数，默认 3")
    parser.add_argument("--page-delay", type=float, default=1.0, help="API 翻页间隔秒数，默认 1.0")
    parser.add_argument("--minimum-bytes", type=int, default=1024, help="文件完整性的最低字节数")
    parser.add_argument("--no-reorg", action="store_true", help="跳过下载后的作者归类")
    parser.add_argument("--reorg-only", action="store_true", help="只运行作者归类（不重新拉取和下载）")
    parser.add_argument("--limit", type=int, default=-1, help="最多拉取条数，0=全部，不传则进界面后选档位")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.workers <= 6:
        print("--workers 必须在 1 到 6 之间", file=sys.stderr)
        return 2
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        # --reorg-only: just run classification on existing files
        if args.reorg_only:
            if not args.collection:
                print("--reorg-only 需要同时指定 --collection", file=sys.stderr)
                return 2
            prepare_profile()
            with BrowserApi(PROFILE_DIR) as api:
                collections = fetch_collections(api)
                collection = select_collection(collections, args.collection)
            state = StateStore(STATE_PATH)
            try:
                author_map = state.get_author_map(collection["id"])
                if not author_map:
                    print("还没有该作者信息，请先完整下载一次以建立作者映射。", file=sys.stderr)
                    return 2
                output_root = OUTPUT_BASE / f"{safe_name(collection['name'], fallback=collection['id'])}_{collection['id']}"
                print(f"\n[*] 按作者归类（仅归类，不下载）…")
                result = reorganize_by_author(output_root, author_map, state, collection["id"])
                if result:
                    for author_name, count in sorted(result.items()):
                        print(f"    {author_name}: {count} 个作品 → 作者/{safe_name(author_name)}/")
                else:
                    print("    （所有作者作品数均 ≤2，无需归类）")
                return 0
            finally:
                state.close()

        collection, works, author_map = fetch_target(args.collection, args.list_only, args.page_delay, args.limit)
        if args.list_only:
            return 0
        return run_downloads(
            collection,
            works,
            args.workers,
            args.minimum_bytes,
            args.retries,
            args.dry_run,
            author_map=author_map,
            skip_reorg=args.no_reorg,
        )
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except CollectionSelectionError as error:
        print(f"选择收藏夹失败：{error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
