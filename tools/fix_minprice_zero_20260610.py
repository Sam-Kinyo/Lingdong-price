# -*- coding: utf-8 -*-
"""
一次性修復腳本（2026-06-10）：後台匯入把 16 筆 397 行動電源的 minPrice 寫成 0，
依 1150507 總表「售價含」欄還原。只更新「目前 minPrice 仍為 0」的文件，寫入前備份。
"""
import datetime
import json
import os

import firebase_admin
from firebase_admin import credentials, firestore

# 分流碼 -> 正確售價（1150507-528-397-525商品.xlsx 397 分頁「售價含」欄）
RESTORE = {
    "397026": 1799, "397027": 1799, "397028": 1799, "397029": 1799,          # X3
    "397040": 1599, "397041": 1599,                                          # H20
    "397070": 1599, "397071": 1599, "397072": 1599, "397073": 1599,          # H20
    "397785": 599, "397786": 599,                                            # H10000
    "397885": 499, "397886": 499,                                            # NC5000
    "397924": 1690, "397925": 1690,                                          # Qz10000 Qi2
}

ROOT = os.path.join(os.path.dirname(__file__), "..")
cred = credentials.Certificate(os.path.join(ROOT, "backend", "serviceAccountKey.json"))
firebase_admin.initialize_app(cred)
db = firestore.client()

# 備份現況
snapshot = {}
for sc in RESTORE:
    d = db.collection("Products").document(sc).get()
    snapshot[sc] = d.to_dict() if d.exists else None

bdir = os.path.join(ROOT, "backups")
os.makedirs(bdir, exist_ok=True)
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bpath = os.path.join(bdir, f"Products_minPrice_fix_{ts}.json")
with open(bpath, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)
print(f"[BACKUP] {bpath}")

# 只修 minPrice 仍為 0 的
fixed, skipped = [], []
for sc, price in RESTORE.items():
    cur = snapshot.get(sc)
    if cur is None:
        skipped.append((sc, "not found"))
        continue
    if cur.get("minPrice") != 0:
        skipped.append((sc, f"minPrice={cur.get('minPrice')!r} 非 0，跳過"))
        continue
    db.collection("Products").document(sc).set({"minPrice": price}, merge=True)
    fixed.append((sc, price))

print(f"[FIXED] {len(fixed)} 筆")
for sc, p in fixed:
    print(f"  {sc} -> {p}")
for sc, why in skipped:
    print(f"[SKIP] {sc}: {why}")

# 驗證
print("[VERIFY]")
ok = True
for sc, price in RESTORE.items():
    v = (db.collection("Products").document(sc).get().to_dict() or {}).get("minPrice")
    mark = "OK" if v == price else "!!"
    if v != price:
        ok = False
    print(f"  {mark} {sc} minPrice={v}")
print("ALL OK" if ok else "HAS MISMATCH")
