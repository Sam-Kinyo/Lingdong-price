"""
一次性遷移：為現有 Products 算好「各等級 × 各數量級距」的報價，寫進 quotes 欄位。

方案 B（報價預存）：前端改讀 Products.quotes 顯示報價，就不需要、也拿不到 cost。
報價 = 無條件進位(cost / 係數 × 1.05)，與後端 pricing_service.DIVISOR_MAP 一致。

quotes 結構：{ "4": {"50":141,...}, "3": {...}, "2": {...}, "1": {...} }
（Firestore 欄位 key 須為字串）

用法：python tools/backfill_quotes.py
"""
import math

import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate(
    r"D:\LINGDONG_PROJECT\lingdong-price\backend\serviceAccountKey.json"
)
firebase_admin.initialize_app(cred)
db = firestore.client()

DIVISOR_MAP = {
    4: {50: 0.75, 100: 0.78, 300: 0.81, 500: 0.835, 1000: 0.858, 3000: 0.89},
    3: {50: 0.75, 100: 0.78, 300: 0.81, 500: 0.835, 1000: 0.858},
    2: {50: 0.74, 100: 0.77, 300: 0.80},
    1: {50: 0.73, 100: 0.76},
}


def quotes_for(cost) -> dict:
    """算出所有等級各級距的報價。回傳 {level: {tier: price}}（key 皆字串）。"""
    try:
        c = float(cost)
    except (TypeError, ValueError):
        return {}
    if c <= 0:
        return {}
    return {
        str(lv): {str(t): int(math.ceil((c / d) * 1.05)) for t, d in tiers.items()}
        for lv, tiers in DIVISOR_MAP.items()
    }


def main():
    docs = list(db.collection("Products").stream())
    batch = db.batch()
    n = total = skipped = 0
    for d in docs:
        q = quotes_for((d.to_dict() or {}).get("cost"))
        if not q:
            skipped += 1
            continue
        batch.update(d.reference, {"quotes": q})
        n += 1
        total += 1
        if n >= 400:
            batch.commit()
            batch = db.batch()
            n = 0
    if n > 0:
        batch.commit()
    print(f"已為 {total} 筆寫入 quotes（跳過無 cost / cost=0：{skipped} 筆）")

    sample = db.collection("Products").document("528851").get().to_dict() or {}
    print("\n驗證 528851 (UB-60L) 的 quotes：")
    print("  ", sample.get("quotes"))


if __name__ == "__main__":
    main()
