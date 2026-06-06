"""
B4：成本價分離（方案 B）。

把 cost 從 Products 複製到 ProductsCost（之後用 rules 設成 admin-only），
再從 Products 移除 cost 欄位。前端 B3 之後已改讀 quotes、不需要 cost，
所以 Products 移除 cost 不影響任何顯示；客戶讀 Products 也再也讀不到成本價。

安全機制：先全部複製、驗證筆數無誤，才從 Products 刪除 cost。
"""
import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate(
    r"D:\LINGDONG_PROJECT\lingdong-price\backend\serviceAccountKey.json"
)
firebase_admin.initialize_app(cred)
db = firestore.client()


def main():
    docs = [(d.id, d.to_dict() or {}) for d in db.collection("Products").stream()]
    have_cost = [(i, dd) for i, dd in docs if dd.get("cost") is not None]
    print(f"Products 共 {len(docs)} 筆，其中有 cost：{len(have_cost)} 筆")

    # 階段 1：複製 cost → ProductsCost
    batch = db.batch()
    n = 0
    for i, dd in have_cost:
        batch.set(db.collection("ProductsCost").document(i),
                  {"splitCode": i, "cost": dd["cost"]})
        n += 1
        if n >= 400:
            batch.commit()
            batch = db.batch()
            n = 0
    if n > 0:
        batch.commit()

    pc_count = len(list(db.collection("ProductsCost").stream()))
    print(f"ProductsCost 已寫入 {pc_count} 筆")
    assert pc_count >= len(have_cost), "❌ 複製不完整，中止（不刪 Products.cost）"

    # 階段 2：從 Products 移除 cost
    batch = db.batch()
    n = 0
    for i, _ in have_cost:
        batch.update(db.collection("Products").document(i),
                     {"cost": firestore.DELETE_FIELD})
        n += 1
        if n >= 400:
            batch.commit()
            batch = db.batch()
            n = 0
    if n > 0:
        batch.commit()
    print(f"已從 {len(have_cost)} 筆 Products 移除 cost 欄位")

    # 驗證
    s = db.collection("Products").document("528851").get().to_dict() or {}
    print(f"\n驗證 528851：Products 還有 cost? {'cost' in s} | 有 quotes? {'quotes' in s}")
    pcs = db.collection("ProductsCost").document("528851").get().to_dict() or {}
    print(f"         ProductsCost.cost = {pcs.get('cost')}")


if __name__ == "__main__":
    main()
