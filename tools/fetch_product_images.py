from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


DEFAULT_INPUT_XLSX = Path(r"c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx")
DEFAULT_OUTPUT_DIR = Path(r"d:\LINGDONG_PROJECT\lingdong-price\downloaded_images")
DEFAULT_REPORT_CSV = Path(r"d:\LINGDONG_PROJECT\lingdong-price\download_report.csv")
DEFAULT_FIREBASE_BUCKET = "lingdong-price.firebasestorage.app"


def to_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def normalize_url(raw_url: str) -> str:
    url = to_text(raw_url)
    if not url:
        return ""
    if not re.match(r"^https?://", url, flags=re.I):
        url = f"https://{url}"
    return url


def sanitize_filename(name: str) -> str:
    name = to_text(name)
    if not name:
        return "unknown"
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")


def unique_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def is_valid_image_url(src: str) -> bool:
    src_low = to_text(src).lower()
    if not src_low:
        return False
    invalid_keywords = ("logo", "icon", "avatar", "placeholder", "spinner", "loading")
    if any(word in src_low for word in invalid_keywords):
        return False
    if src_low.startswith("data:") or src_low.endswith(".svg"):
        return False
    return True


def extract_image_candidates(page_url: str, html: str, img_selector: str | None = None) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    meta_selectors = [
        ("meta", {"property": "og:image"}),
        ("meta", {"name": "og:image"}),
        ("meta", {"property": "twitter:image"}),
        ("meta", {"name": "twitter:image"}),
    ]
    for tag_name, attrs in meta_selectors:
        tag = soup.find(tag_name, attrs=attrs)
        if tag:
            content = to_text(tag.get("content"))
            if content:
                candidates.append(urljoin(page_url, content))

    image_src_link = soup.find("link", attrs={"rel": "image_src"})
    if image_src_link:
        href = to_text(image_src_link.get("href"))
        if href:
            candidates.append(urljoin(page_url, href))

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = to_text(script.string)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        def collect_images(obj: object) -> None:
            if isinstance(obj, dict):
                image_value = obj.get("image")
                if isinstance(image_value, str):
                    candidates.append(urljoin(page_url, image_value))
                elif isinstance(image_value, list):
                    for sub in image_value:
                        if isinstance(sub, str):
                            candidates.append(urljoin(page_url, sub))
                for v in obj.values():
                    collect_images(v)
            elif isinstance(obj, list):
                for v in obj:
                    collect_images(v)

        collect_images(payload)

    # 客製 selector 優先，可讓你針對特定網站強制抓指定圖片
    if img_selector:
        for tag in soup.select(img_selector):
            src = to_text(tag.get("src") or tag.get("data-src") or tag.get("data-original"))
            if src:
                candidates.insert(0, urljoin(page_url, src))

    # 後備：抓頁面第一批可用 img
    for img in soup.find_all("img"):
        src = to_text(img.get("data-src") or img.get("data-original") or img.get("src"))
        if src:
            candidates.append(urljoin(page_url, src))

    # 排除 data URI / SVG icon / 空白
    cleaned = [c for c in candidates if is_valid_image_url(c)]

    return unique_keep_order(cleaned)


def guess_extension(image_url: str, content_type: str) -> str:
    content_type = to_text(content_type).lower()
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"

    path = urlparse(image_url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def download_binary(session: requests.Session, url: str, timeout: int) -> tuple[bytes, str]:
    resp = session.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    content_type = to_text(resp.headers.get("Content-Type"))
    data = resp.content
    if not data:
        raise ValueError("empty image content")
    return data, content_type


def throttle_by_host(url: str, lock: threading.Lock, host_next_allowed: dict[str, float], min_host_interval: float) -> None:
    if min_host_interval <= 0:
        return
    host = urlparse(url).netloc.lower() or "__default__"
    while True:
        with lock:
            now = time.time()
            next_allowed = host_next_allowed.get(host, 0.0)
            if now >= next_allowed:
                host_next_allowed[host] = now + min_host_interval
                return
            wait_s = max(0.0, next_allowed - now)
        time.sleep(min(wait_s, 0.3))


def get_with_retry(
    session: requests.Session,
    url: str,
    timeout: int,
    max_retries: int,
    retry_delay: float,
    lock: threading.Lock,
    host_next_allowed: dict[str, float],
    min_host_interval: float,
) -> requests.Response:
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            throttle_by_host(url, lock, host_next_allowed, min_host_interval)
            resp = session.get(url, timeout=timeout)
            status = int(resp.status_code)
            if status in (429, 500, 502, 503, 504):
                retry_after = to_text(resp.headers.get("Retry-After"))
                wait_s = float(retry_after) if retry_after.isdigit() else retry_delay * (2 ** attempt)
                time.sleep(max(0.3, wait_s))
                last_err = RuntimeError(f"HTTP {status}")
                continue
            resp.raise_for_status()
            return resp
        except Exception as ex:
            last_err = ex
            if attempt < max_retries:
                time.sleep(max(0.3, retry_delay * (2 ** attempt)))
            else:
                break

    raise RuntimeError(f"request failed: {url} -> {last_err}")


def find_existing_image(output_dir: Path, base: str) -> Path | None:
    patterns = [f"{base}.jpg", f"{base}.jpeg", f"{base}.png", f"{base}.webp", f"{base}.gif"]
    for name in patterns:
        p = output_dir / name
        if p.exists():
            return p
    return None


def build_output_file(output_dir: Path, base: str, ext: str, row_index: int, overwrite: bool) -> Path:
    out_file = output_dir / f"{base}{ext}"
    if overwrite or not out_file.exists():
        return out_file
    return output_dir / f"{base}_r{row_index}{ext}"


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )
    return session


def init_firebase_clients(cred_path: str, bucket_name: str):
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore, storage
    except Exception as ex:
        raise RuntimeError("firebase-admin 未安裝，請先執行: pip install firebase-admin") from ex

    options = {"storageBucket": bucket_name}
    if cred_path:
        cred_file = Path(cred_path)
        if not cred_file.exists():
            raise FileNotFoundError(f"找不到 Firebase 憑證檔: {cred_file}")
        cred = credentials.Certificate(str(cred_file))
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, options)
    else:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options=options)

    db = firestore.client()
    bucket = storage.bucket(bucket_name)
    return db, firestore, bucket


def upload_image_to_storage(
    bucket,
    object_name: str,
    binary: bytes,
    content_type: str,
    make_public: bool = False,
) -> str:
    blob = bucket.blob(object_name)
    final_content_type = content_type or mimetypes.guess_type(object_name)[0] or "application/octet-stream"
    blob.cache_control = "public, max-age=31536000"
    blob.upload_from_string(binary, content_type=final_content_type)

    if make_public:
        blob.make_public()
        return blob.public_url

    token = str(uuid.uuid4())
    blob.metadata = {"firebaseStorageDownloadTokens": token}
    blob.patch()
    encoded_name = quote(object_name, safe="")
    return f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{encoded_name}?alt=media&token={token}"


def update_firestore_image_urls(rows: list[dict[str, str]], args: argparse.Namespace) -> tuple[int, int]:
    db, fs_admin, _ = init_firebase_clients(args.firebase_cred, args.firebase_bucket)
    ok = 0
    fail = 0
    target_rows = [r for r in rows if r.get("status") == "ok"]
    total_targets = len(target_rows)

    for idx, row in enumerate(target_rows, start=1):
        doc_id = to_text(row.get("splitCode") or row.get("model"))
        image_url = to_text(row.get("imageUrl"))
        if not doc_id or not image_url:
            continue

        payload = {
            args.firestore_image_field: image_url,
            "imageSource": "crawler",
            "imageUpdatedAt": fs_admin.SERVER_TIMESTAMP,
        }

        last_err = ""
        for attempt in range(args.firestore_retries + 1):
            try:
                db.collection(args.firebase_collection).document(doc_id).set(payload, merge=True)
                ok += 1
                if args.progress:
                    print(f"[FIRESTORE {idx}/{total_targets}] ok {doc_id}")
                last_err = ""
                break
            except Exception as ex:
                last_err = str(ex)
                if attempt < args.firestore_retries:
                    time.sleep(max(0.5, args.firestore_retry_delay * (2 ** attempt)))
                else:
                    break

        if last_err:
            fail += 1
            if args.progress:
                print(f"[FIRESTORE {idx}/{total_targets}] failed {doc_id} -> {last_err}")

    return ok, fail


def process_one_row(
    row_index: int,
    row_data: dict[str, Any],
    args: argparse.Namespace,
    lock: threading.Lock,
    host_next_allowed: dict[str, float],
    bucket,
) -> dict[str, str]:
    model = sanitize_filename(to_text(row_data.get(args.model_col)) or f"row{row_index+1}")
    split_code = sanitize_filename(to_text(row_data.get(args.split_col, "")))
    page_url = normalize_url(to_text(row_data.get(args.url_col)))
    base_name = f"{model}_{split_code}" if split_code else model

    if not page_url:
        return {
            "row": str(row_index + 2),
            "model": model,
            "splitCode": split_code,
            "pageUrl": "",
            "imageUrl": "",
            "status": "failed",
            "message": "empty product url",
            "savedPath": "",
        }

    if args.save_local and args.only_new and not args.overwrite:
        existing = find_existing_image(args.output_dir, base_name)
        if existing:
            return {
                "row": str(row_index + 2),
                "model": model,
                "splitCode": split_code,
                "pageUrl": page_url,
                "imageUrl": "",
                "status": "skipped_existing",
                "message": "already downloaded",
                "savedPath": str(existing),
            }

    session = create_session()
    try:
        page_resp = get_with_retry(
            session=session,
            url=page_url,
            timeout=args.timeout,
            max_retries=args.retries,
            retry_delay=args.retry_delay,
            lock=lock,
            host_next_allowed=host_next_allowed,
            min_host_interval=args.min_host_interval,
        )
        candidates = extract_image_candidates(
            page_url=page_url,
            html=page_resp.text,
            img_selector=to_text(args.img_selector) or None,
        )
        if not candidates:
            raise ValueError("no image candidate found from page")

        last_err = ""
        for image_url in candidates[: args.max_candidates]:
            try:
                img_resp = get_with_retry(
                    session=session,
                    url=image_url,
                    timeout=args.timeout,
                    max_retries=args.retries,
                    retry_delay=args.retry_delay,
                    lock=lock,
                    host_next_allowed=host_next_allowed,
                    min_host_interval=args.min_host_interval,
                )
                out_file = None
                final_image_url = image_url
                binary = img_resp.content
                if not binary:
                    raise ValueError("empty image content")
                content_type = to_text(img_resp.headers.get("Content-Type"))

                if args.upload_to_storage:
                    ext = guess_extension(image_url, content_type)
                    safe_model = re.sub(r"[^a-zA-Z0-9_.-]+", "_", model).strip("._") or f"row{row_index+2}"
                    safe_split = re.sub(r"[^a-zA-Z0-9_.-]+", "_", split_code).strip("._") if split_code else ""
                    fname = f"{safe_model}_{safe_split}{ext}" if safe_split else f"{safe_model}{ext}"
                    object_name = f"{args.storage_prefix.strip('/')}/{fname}" if args.storage_prefix else fname
                    final_image_url = upload_image_to_storage(
                        bucket=bucket,
                        object_name=object_name,
                        binary=binary,
                        content_type=content_type,
                        make_public=args.storage_make_public,
                    )

                if args.save_local:
                    ext = guess_extension(image_url, content_type)
                    out_file = build_output_file(args.output_dir, base_name, ext, row_index + 2, args.overwrite)
                    out_file.write_bytes(binary)
                return {
                    "row": str(row_index + 2),
                    "model": model,
                    "splitCode": split_code,
                    "pageUrl": page_url,
                    "imageUrl": final_image_url,
                    "status": "ok",
                    "message": "",
                    "savedPath": str(out_file) if out_file else "",
                }
            except Exception as ex:
                last_err = str(ex)

        raise ValueError(f"all candidates failed: {last_err or 'unknown error'}")
    except Exception as ex:
        return {
            "row": str(row_index + 2),
            "model": model,
            "splitCode": split_code,
            "pageUrl": page_url,
            "imageUrl": "",
            "status": "failed",
            "message": str(ex),
            "savedPath": "",
        }
    finally:
        session.close()
        if args.delay > 0:
            time.sleep(args.delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="依 Excel 商品網址爬取對應商品圖片")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_XLSX, help="Excel 路徑")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="圖片輸出資料夾")
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_CSV, help="報表 CSV 路徑")
    parser.add_argument("--sheet", default="", help="工作表名稱（空白=第一張）")
    parser.add_argument("--model-col", default="型號", help="型號欄名")
    parser.add_argument("--url-col", default="商品對應網站", help="網址欄名")
    parser.add_argument("--split-col", default="分流", help="分流欄名")
    parser.add_argument("--delay", type=float, default=0.15, help="每筆抓取完成後等待秒數")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout 秒數")
    parser.add_argument("--overwrite", action="store_true", help="是否覆蓋既有檔案")
    parser.add_argument("--img-selector", default="", help="指定 CSS selector 強制抓圖")
    parser.add_argument("--workers", type=int, default=4, help="平行執行緒數（建議 2~6）")
    parser.add_argument("--retries", type=int, default=3, help="每個 URL 最多重試次數")
    parser.add_argument("--retry-delay", type=float, default=1.2, help="重試基礎等待秒數（退避會倍增）")
    parser.add_argument("--min-host-interval", type=float, default=0.35, help="同網域最小請求間隔秒數")
    parser.add_argument("--max-candidates", type=int, default=8, help="每頁最多嘗試幾個圖片候選")
    parser.add_argument(
        "--save-local",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否儲存圖片到本機（預設 true）",
    )
    parser.add_argument(
        "--only-new",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="只抓新增（預設 true，已下載會略過）",
    )
    parser.add_argument(
        "--update-firestore",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="抓圖成功後是否直接更新 Firestore（寫入 imageUrl）",
    )
    parser.add_argument("--firebase-cred", default="", help="Firebase service account JSON 路徑")
    parser.add_argument("--firebase-bucket", default=DEFAULT_FIREBASE_BUCKET, help="Firebase Storage bucket 名稱")
    parser.add_argument("--firebase-collection", default="Products", help="Firestore 集合名稱")
    parser.add_argument("--firestore-image-field", default="imageUrl", help="Firestore 圖片欄位名稱")
    parser.add_argument("--firestore-retries", type=int, default=3, help="Firestore 更新重試次數")
    parser.add_argument("--firestore-retry-delay", type=float, default=1.0, help="Firestore 重試基礎等待秒數")
    parser.add_argument(
        "--upload-to-storage",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="是否把抓到的圖片上傳到 Firebase Storage，並回填 Storage URL",
    )
    parser.add_argument("--storage-prefix", default="product-images", help="Storage 路徑前綴")
    parser.add_argument(
        "--storage-make-public",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="上傳後是否直接設為 public（預設使用 download token URL）",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否輸出即時進度（預設 true）",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"找不到 Excel: {args.input}")

    xls = pd.ExcelFile(args.input)
    sheet_name = args.sheet or xls.sheet_names[0]
    df = pd.read_excel(args.input, sheet_name=sheet_name, dtype=str)

    for col in (args.model_col, args.url_col):
        if col not in df.columns:
            raise ValueError(f"缺少必要欄位: {col}")

    if args.save_local:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report_csv.parent.mkdir(parents=True, exist_ok=True)

    report_rows: list[dict[str, str]] = []
    ok_count = 0
    fail_count = 0
    skipped_count = 0

    rows = [row.to_dict() for _, row in df.iterrows()]
    total_rows = len(rows)
    host_lock = threading.Lock()
    host_next_allowed: dict[str, float] = {}
    bucket = None

    if args.upload_to_storage or args.update_firestore:
        _, _, bucket = init_firebase_clients(args.firebase_cred, args.firebase_bucket)

    max_workers = max(1, min(int(args.workers), 16))
    if args.progress:
        print(
            f"[START] total={total_rows}, workers={max_workers}, "
            f"only_new={args.only_new}, save_local={args.save_local}, "
            f"upload_to_storage={args.upload_to_storage}, update_firestore={args.update_firestore}"
        )

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_one_row, idx, row_data, args, host_lock, host_next_allowed, bucket)
            for idx, row_data in enumerate(rows)
        ]
        for future in as_completed(futures):
            row_result = future.result()
            report_rows.append(row_result)
            completed += 1
            status = row_result.get("status", "")
            if status == "ok":
                ok_count += 1
            elif status == "skipped_existing":
                skipped_count += 1
            else:
                fail_count += 1
            if args.progress:
                model = to_text(row_result.get("model"))
                split_code = to_text(row_result.get("splitCode"))
                image_url = to_text(row_result.get("imageUrl"))
                message = to_text(row_result.get("message"))
                extra = image_url or message
                print(f"[{completed}/{total_rows}] {status} model={model} split={split_code} {extra}".strip())

    report_rows.sort(key=lambda x: int(x["row"]))

    with args.report_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["row", "model", "splitCode", "pageUrl", "imageUrl", "status", "message", "savedPath"],
        )
        writer.writeheader()
        writer.writerows(report_rows)

    if args.update_firestore:
        fs_ok, fs_fail = update_firestore_image_urls(report_rows, args)
        print(f"[FIRESTORE] updated={fs_ok}, failed={fs_fail}, collection={args.firebase_collection}")

    print(f"[DONE] success={ok_count}, skipped={skipped_count}, failed={fail_count}")
    if args.save_local:
        print(f"[IMAGES] {args.output_dir}")
    else:
        print("[IMAGES] local save disabled (--no-save-local)")
    print(f"[REPORT] {args.report_csv}")


if __name__ == "__main__":
    main()
