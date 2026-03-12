from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


INPUT_XLSX = Path(r"c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx")
OUTPUT_JSON = Path(r"d:\LINGDONG_PROJECT\lingdong-price\products_local.json")
REQUIRED_COLUMNS = [
    "品牌",
    "分類",
    "分流",
    "國際條碼",
    "型號",
    "商品名稱",
    "詢價\n含",
    "市價\n含",
    "售價\n含",
    "箱入數",
    "BSMI",
    "NCC",
    "狀態",
    "商品對應網站",
]
# 狀態映射規則（與 README 保持一致）
# - 停產/下架 -> inactive（前台不顯示）
# - 一般商品/缺貨中 -> active（前台顯示）


def txt(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def price(v) -> float:
    s = txt(v).replace(",", "")
    if not s:
        return 0.0
    return float(s)


def normalize_barcode(v) -> str:
    s = txt(v).replace(",", "")
    if not s:
        return ""
    if s.endswith(".0"):
        return s[:-2]
    return s


def normalize_status(v) -> str:
    s = txt(v)
    if s in ("下架", "停產"):
        return "inactive"
    return "active"


def default_inventory(status_text: str) -> int:
    # 目前不做真實庫存，先用狀態給前端概略顯示
    return 0 if status_text == "缺貨中" else 200


def main() -> None:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else INPUT_XLSX
    xls = pd.ExcelFile(input_path)
    # 規則：未來固定只有一頁，若多頁只取第一頁並給提示
    if len(xls.sheet_names) > 1:
        print(f"[WARN] workbook has {len(xls.sheet_names)} sheets, only first sheet will be used: {xls.sheet_names[0]}")

    sheet_name = xls.sheet_names[0]
    df = pd.read_excel(input_path, sheet_name=sheet_name, dtype=str)

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")

    rows = []
    split_code_seen = set()
    for _, r in df.iterrows():
        split_code = txt(r.get("分流", ""))
        model = txt(r.get("型號", "")).upper()
        if not split_code:
            # 分流是你定義的唯一鍵，沒有就跳過
            continue
        if split_code in split_code_seen:
            # 同分流重複時，以最後一筆覆蓋（更新語意）
            rows = [x for x in rows if x.get("splitCode") != split_code]
        split_code_seen.add(split_code)

        status_text = txt(r.get("狀態", ""))
        row = {
            "id": split_code,
            "splitCode": split_code,
            "brand": txt(r.get("品牌", "")),
            "category": txt(r.get("分類", "")),
            "model": model or split_code,
            "name": txt(r.get("商品名稱", "")) or model or split_code,
            "cost": price(r.get("詢價\n含", "")),
            "marketPrice": price(r.get("市價\n含", "")),
            "minPrice": price(r.get("售價\n含", "")),
            # 目前不做真實庫存，以狀態給前端大致顯示。
            "inventory": default_inventory(status_text),
            "eta": "",
            "status": normalize_status(status_text),
            "statusText": status_text,
            "isControlled": False,
            "productUrl": txt(r.get("商品對應網站", "")),
            "barcode": normalize_barcode(r.get("國際條碼", "")),
            "internationalBarcode": normalize_barcode(r.get("國際條碼", "")),
            "cartonQty": int(float(txt(r.get("箱入數", "0")) or 0)),
            "bsmi": txt(r.get("BSMI", "")),
            "ncc": txt(r.get("NCC", "")),
            "netSalesPermission": "",
            "sourceSheet": txt(sheet_name),
        }
        rows.append(row)

    final_rows = sorted(rows, key=lambda x: x["splitCode"])

    OUTPUT_JSON.write_text(
        json.dumps(final_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"rows={len(final_rows)} -> {OUTPUT_JSON} (unique by splitCode)")


if __name__ == "__main__":
    main()
