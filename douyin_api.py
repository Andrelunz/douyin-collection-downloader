from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import quote

from downloader_core import Work, dedupe_works, parse_aweme

BASE_QUERY = "device_platform=webapp&aid=6383&channel=channel_pc_web"
HOME_URL = "https://www.douyin.com/user/self?showTab=favorite_collection"


def chrome_options(profile_dir: Path):
    from DrissionPage import ChromiumOptions

    options = ChromiumOptions()
    options.set_user_data_path(str(profile_dir.resolve()))
    options.set_argument("--no-proxy-server")
    options.set_argument("--start-maximized")
    for browser in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ):
        if browser.exists():
            options.set_browser_path(str(browser))
            break
    return options


def is_logged_in(page) -> bool:
    try:
        for cookie in page.cookies():
            name = cookie.get("name") if isinstance(cookie, dict) else cookie.name
            if name in {"sessionid", "sessionid_ss"}:
                return True
    except Exception:
        return False
    return False


def wait_for_login(page, timeout: int = 300) -> None:
    print("\n需要登录：请在弹出的抖音窗口中扫码（登录状态会保存在本项目内）。")
    try:
        page.set.window.max()
        page.set.window.foreground()
    except Exception:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_logged_in(page):
            print("登录成功。")
            time.sleep(3)
            return
        time.sleep(2)
    raise TimeoutError(f"等待扫码登录超时（{timeout} 秒）")


class BrowserApi:
    def __init__(self, profile_dir: Path):
        from DrissionPage import ChromiumPage

        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self.page = ChromiumPage(chrome_options(profile_dir))

    def __enter__(self) -> "BrowserApi":
        self.page.get(HOME_URL)
        time.sleep(7)
        if not is_logged_in(self.page):
            wait_for_login(self.page)
            self.page.get(HOME_URL)
            time.sleep(7)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.page.quit()

    def fetch_json(self, url: str, retries: int = 3, timeout: float = 18.0) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return self._fetch_once(url, timeout)
            except Exception as error:
                last_error = error
                if attempt == retries:
                    break
                try:
                    self.page.get(HOME_URL)
                    time.sleep(5)
                except Exception:
                    pass
                time.sleep(attempt * 2)
        raise RuntimeError(f"抖音 API 请求失败：{url}；{last_error}") from last_error

    def _fetch_once(self, url: str, timeout: float) -> dict:
        url_json = json.dumps(url, ensure_ascii=False)
        self.page.run_js(
            "window.__hermes_api_result='';"
            f"fetch({url_json}, {{credentials:'include'}})"
            ".then(async r => {const t=await r.text();"
            "window.__hermes_api_result=JSON.stringify({status:r.status,text:t});})"
            ".catch(e => {window.__hermes_api_result=JSON.stringify({error:String(e)});});"
        )
        deadline = time.time() + timeout
        raw = ""
        while time.time() < deadline:
            time.sleep(0.35)
            raw = self.page.run_js("return window.__hermes_api_result") or ""
            if raw:
                break
        if not raw:
            raise TimeoutError(f"API {timeout:.0f} 秒内未返回")
        wrapper = json.loads(raw)
        if wrapper.get("error"):
            raise RuntimeError(wrapper["error"])
        if int(wrapper.get("status", 0)) != 200:
            raise RuntimeError(f"HTTP {wrapper.get('status')}")
        payload = json.loads(wrapper.get("text") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("API 返回的不是 JSON 对象")
        if payload.get("status_code") not in (None, 0):
            raise RuntimeError(
                f"API status_code={payload.get('status_code')}: "
                f"{payload.get('status_msg') or payload.get('message') or '未知错误'}"
            )
        return payload


def extract_collections(data: dict) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for item in data.get("collects_list") or []:
        collection_id = str(
            item.get("collects_id") or item.get("collects_id_str") or ""
        ).strip()
        if not collection_id or collection_id in seen:
            continue
        seen.add(collection_id)
        name = str(item.get("collects_name") or "未命名").strip()
        count = item.get("total_number")
        if count is None:
            count = item.get("vcount", item.get("total_count", 0))
        result.append({"id": collection_id, "name": name, "count": int(count or 0)})
    return result


def fetch_collections(api) -> list[dict]:
    profile = api.fetch_json(f"/aweme/v1/web/user/profile/self/?{BASE_QUERY}")
    sec_uid = str((profile.get("user") or {}).get("sec_uid") or profile.get("sec_uid") or "")
    url = (
        f"/aweme/v1/web/collects/list/?{BASE_QUERY}&count=100&cursor=0"
        f"&sec_user_id={quote(sec_uid)}"
    )
    return extract_collections(api.fetch_json(url))


def fetch_collection_works(
    api,
    collection_id: str,
    *,
    page_delay: float = 1.0,
    max_pages: int = 500,
    max_works: int = 0,
    progress=None,
) -> tuple[list[Work], dict[str, str]]:
    works: list[Work | None] = []
    seen_ids: set[str] = set()
    author_map: dict[str, str] = {}
    cursor: int | str = 0

    for page_number in range(1, max_pages + 1):
        url = (
            f"/aweme/v1/web/collects/video/list/?{BASE_QUERY}"
            f"&collects_id={quote(str(collection_id))}&count=18&cursor={cursor}"
        )
        data = api.fetch_json(url)
        for aweme in data.get("aweme_list") or []:
            work = parse_aweme(aweme)
            if work and work.work_id not in seen_ids:
                seen_ids.add(work.work_id)
                works.append(work)
            # Collect author info
            wid = str(aweme.get("aweme_id") or aweme.get("id") or "").strip()
            author = (aweme.get("author") or {}).get("nickname") or ""
            if wid and author and wid not in author_map:
                author_map[wid] = str(author)
        if progress:
            progress(page_number, len(seen_ids))
        if max_works and len(seen_ids) >= max_works:
            break
        if not bool(data.get("has_more")):
            break
        next_cursor = data.get("cursor")
        if next_cursor is None or str(next_cursor) == str(cursor):
            break
        cursor = next_cursor
        if page_delay:
            time.sleep(page_delay)
    else:
        raise RuntimeError(f"达到分页安全上限 {max_pages}，为避免死循环已停止")

    return dedupe_works(works), author_map
