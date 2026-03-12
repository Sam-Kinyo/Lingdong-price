from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


DEFAULT_INPUT_XLSX = Path(r"c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx")
DEFAULT_OUTPUT_DIR = Path(r"d:\LINGDONG_PROJECT\lingdong-price\downloaded_images")
DEFAULT_REPORT_CSV = Path(r"d:\LINGDONG_PROJECT\lingdong-price\download_report.csv")


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
    cleaned: list[str] = []
    for c in candidates:
        if not c:
            continue
        if c.startswith("data:"):
            continue
        low = c.lower()
        if low.endswith(".svg"):
            continue
        cleaned.append(c)

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


def main() -> None:
    parser = argparse.ArgumentParser(description="依 Excel 商品網址爬取對應商品圖片")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_XLSX, help="Excel 路徑")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="圖片輸出資料夾")
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_CSV, help="報表 CSV 路徑")
    parser.add_argument("--sheet", default="", help="工作表名稱（空白=第一張）")
    parser.add_argument("--model-col", default="型號", help="型號欄名")
    parser.add_argument("--url-col", default="商品對應網站", help="網址欄名")
    parser.add_argument("--split-col", default="分流", help="分流欄名")
    parser.add_argument("--delay", type=float, default=0.2, help="每筆抓取間隔秒數")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout 秒數")
    parser.add_argument("--overwrite", action="store_true", help="是否覆蓋既有檔案")
    parser.add_argument("--img-selector", default="", help="指定 CSS selector 強制抓圖")
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"找不到 Excel: {args.input}")

    xls = pd.ExcelFile(args.input)
    sheet_name = args.sheet or xls.sheet_names[0]
    df = pd.read_excel(args.input, sheet_name=sheet_name, dtype=str)

    for col in (args.model_col, args.url_col):
        if col not in df.columns:
            raise ValueError(f"缺少必要欄位: {col}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report_csv.parent.mkdir(parents=True, exist_ok=True)

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

    report_rows: list[dict[str, str]] = []
    ok_count = 0
    fail_count = 0

    for i, row in df.iterrows():
        model = sanitize_filename(to_text(row.get(args.model_col)) or f"row{i+1}")
        split_code = sanitize_filename(to_text(row.get(args.split_col, "")))
        page_url = normalize_url(to_text(row.get(args.url_col)))

        if not page_url:
            fail_count += 1
            report_rows.append(
                {
                    "row": str(i + 2),
                    "model": model,
                    "splitCode": split_code,
                    "pageUrl": "",
                    "imageUrl": "",
                    "status": "failed",
                    "message": "empty product url",
                    "savedPath": "",
                }
            )
            continue

        try:
            page_resp = session.get(page_url, timeout=args.timeout)
            page_resp.raise_for_status()
            candidates = extract_image_candidates(
                page_url=page_url,
                html=page_resp.text,
                img_selector=to_text(args.img_selector) or None,
            )
            if not candidates:
                raise ValueError("no image candidate found from page")

            last_err = ""
            saved_path = ""
            used_image_url = ""
            for image_url in candidates[:8]:
                try:
                    binary, content_type = download_binary(session, image_url, timeout=args.timeout)
                    ext = guess_extension(image_url, content_type)
                    base = f"{model}_{split_code}" if split_code else model
                    out_file = args.output_dir / f"{base}{ext}"
                    if out_file.exists() and not args.overwrite:
                        saved_path = str(out_file)
                        used_image_url = image_url
                        break
                    out_file.write_bytes(binary)
                    saved_path = str(out_file)
                    used_image_url = image_url
                    break
                except Exception as ex:
                    last_err = str(ex)

            if not saved_path:
                raise ValueError(f"all candidates failed: {last_err or 'unknown error'}")

            ok_count += 1
            report_rows.append(
                {
                    "row": str(i + 2),
                    "model": model,
                    "splitCode": split_code,
                    "pageUrl": page_url,
                    "imageUrl": used_image_url,
                    "status": "ok",
                    "message": "",
                    "savedPath": saved_path,
                }
            )
        except Exception as ex:
            fail_count += 1
            report_rows.append(
                {
                    "row": str(i + 2),
                    "model": model,
                    "splitCode": split_code,
                    "pageUrl": page_url,
                    "imageUrl": "",
                    "status": "failed",
                    "message": str(ex),
                    "savedPath": "",
                }
            )

        if args.delay > 0:
            time.sleep(args.delay)

    with args.report_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["row", "model", "splitCode", "pageUrl", "imageUrl", "status", "message", "savedPath"],
        )
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"[DONE] success={ok_count}, failed={fail_count}")
    print(f"[IMAGES] {args.output_dir}")
    print(f"[REPORT] {args.report_csv}")


if __name__ == "__main__":
    main()
