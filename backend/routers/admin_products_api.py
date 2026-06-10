"""
routers/admin_products_api.py
─────────────────────────────
商品管理 API（管理員專用，level >= 4）。

供 Web 後台「商品管理」面板呼叫：
  GET    /api/admin/products              列出所有商品（含 cost，admin 可見）
  POST   /api/admin/products              新增商品
  PUT    /api/admin/products/{splitCode}  編輯商品（合併欄位，保留圖片等未送欄位）
  DELETE /api/admin/products/{splitCode}  刪除商品（硬刪；停產請改用 PUT status=inactive）

設計重點（方案 B：報價預存、成本分離）：
- cost **絕不寫進 Products**；寫到 `ProductsCost/{splitCode}`（rules 設 admin-only）。
- 收到 cost 時，後端用 `pricing_service.compute_all_quotes` 算好 `Products.quotes` 預存，
  前端/LINE 直接讀 quotes，永遠拿不到 cost。
- 圖片相關欄位（imageUrl/mainImage/netImages/driveMapping/folderUrl…）由命令列同步管理，
  本 API 採白名單只寫可編輯欄位 + 用 merge，避免覆蓋掉它們。
- 任何寫入後呼叫 load_all_products() 刷新記憶體快取，LINE/查價即時反映。
- 停產 = status="inactive"（load_all_products 會跳過，等同從查價結果下架）。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from core.security import verify_admin
from core.config import db
from database.firestore_db import load_all_products
from services.pricing_service import compute_all_quotes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin-Products"])

# 列表回傳的欄位（刻意不含笨重的圖片欄位；含 quotes 供顯示，cost 另外補）
LIST_FIELDS: tuple[str, ...] = (
    "splitCode", "name", "model", "mainModel", "brand", "category",
    "marketPrice", "minPrice", "inventory", "eta", "status", "statusText",
    "internationalBarcode", "barcode", "cartonQty", "isControlled", "quotes",
    "mainImage", "imageUrl", "netImages",
)


# ═══════════════════════════════════════════
# Request Model（create / update 共用；欄位皆選填，None 不寫）
# ═══════════════════════════════════════════
class ProductUpsertBody(BaseModel):
    splitCode: str | None = None          # create 時必填；update 走 path
    name: str | None = None
    model: str | None = None
    mainModel: str | None = None
    brand: str | None = None
    category: str | None = None
    marketPrice: float | None = None
    minPrice: float | None = None
    inventory: int | None = None
    eta: str | None = None
    status: str | None = None             # "active" / "inactive"
    statusText: str | None = None
    internationalBarcode: str | None = None
    barcode: str | None = None
    cartonQty: int | None = None
    isControlled: bool | None = None
    bsmi: str | None = None
    ncc: str | None = None
    productUrl: str | None = None
    mainImage: str | None = None          # 商品大圖網址
    imageUrl: str | None = None           # 前端主要顯示用大圖（通常同 mainImage）
    netImages: list[str] | None = None    # 網路圖（多張網址）
    cost: float | None = None             # → ProductsCost + 重算 quotes（不寫進 Products）


class ImportBody(BaseModel):
    products: list[ProductUpsertBody]


# 總表慣例：價格欄 0 = 未提供/無特價（如「最低特價含」整欄 0），寫入會蓋掉線上售價
ZERO_MEANS_UNSET = ("cost", "marketPrice", "minPrice")


def _dump_clean(body: ProductUpsertBody) -> dict:
    data = body.model_dump(exclude_none=True)
    for f in ZERO_MEANS_UNSET:
        if data.get(f) == 0:
            data.pop(f)
    return data


# ═══════════════════════════════════════════
# GET /api/admin/products
# ═══════════════════════════════════════════
@router.get("/products")
async def list_products(_admin: dict = Depends(verify_admin)) -> dict:
    """列出所有商品（含停產），合併 ProductsCost 的 cost（僅 admin 可見）。"""
    costs: dict[str, float | None] = {}
    for c in db.collection("ProductsCost").stream():
        costs[c.id] = (c.to_dict() or {}).get("cost")

    items: list[dict] = []
    for d in db.collection("Products").stream():
        p = d.to_dict() or {}
        row = {k: p.get(k) for k in LIST_FIELDS if k in p}
        row["splitCode"] = p.get("splitCode") or d.id
        row["cost"] = costs.get(d.id)
        items.append(row)

    items.sort(key=lambda x: str(x.get("splitCode")))
    logger.info(f"📦 admin 列出 {len(items)} 筆商品")
    return {"count": len(items), "products": items}


# ═══════════════════════════════════════════
# POST /api/admin/products
# ═══════════════════════════════════════════
@router.post("/products", status_code=201)
async def create_product(
    body: ProductUpsertBody,
    _admin: dict = Depends(verify_admin),
) -> dict:
    """新增商品：寫 Products（資訊 + 由 cost 算好的 quotes）+ ProductsCost，刷快取。"""
    sc = (body.splitCode or "").strip()
    if not sc:
        raise HTTPException(status_code=400, detail="splitCode（商品編號）必填")

    ref = db.collection("Products").document(sc)
    if ref.get().exists:
        raise HTTPException(status_code=409, detail=f"商品已存在：{sc}")

    data = _dump_clean(body)
    cost = data.pop("cost", None)
    data.pop("splitCode", None)
    data["splitCode"] = sc
    data.setdefault("status", "active")
    if cost is not None:
        data["quotes"] = compute_all_quotes(cost)

    ref.set(data)
    if cost is not None:
        db.collection("ProductsCost").document(sc).set({"splitCode": sc, "cost": cost})

    await load_all_products()
    logger.info(f"➕ admin 新增商品 {sc}（cost={'有' if cost is not None else '無'}）")
    return {"splitCode": sc, "created": True, "hasCost": cost is not None}


# ═══════════════════════════════════════════
# PUT /api/admin/products/{split_code}
# ═══════════════════════════════════════════
@router.put("/products/{split_code}")
async def update_product(
    body: ProductUpsertBody,
    split_code: str = Path(..., description="商品編號 splitCode"),
    _admin: dict = Depends(verify_admin),
) -> dict:
    """編輯商品：合併送來的欄位（保留圖片等未送欄位）；附 cost 則更新 ProductsCost + 重算 quotes。"""
    sc = split_code.strip()
    ref = db.collection("Products").document(sc)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail=f"找不到商品：{sc}")

    data = _dump_clean(body)
    cost = data.pop("cost", None)
    data.pop("splitCode", None)  # splitCode 不可改（doc id）
    if cost is not None:
        data["quotes"] = compute_all_quotes(cost)

    if data:
        ref.set(data, merge=True)
    if cost is not None:
        db.collection("ProductsCost").document(sc).set({"splitCode": sc, "cost": cost})

    await load_all_products()
    logger.info(f"✏️ admin 編輯商品 {sc} 欄位={list(data.keys())} costUpdated={cost is not None}")
    return {
        "splitCode": sc,
        "updated": True,
        "fields": list(data.keys()),
        "costUpdated": cost is not None,
    }


# ═══════════════════════════════════════════
# DELETE /api/admin/products/{split_code}
# ═══════════════════════════════════════════
@router.delete("/products/{split_code}")
async def delete_product(
    split_code: str = Path(..., description="商品編號 splitCode"),
    _admin: dict = Depends(verify_admin),
) -> dict:
    """硬刪商品（含 ProductsCost）。一般「停產」建議用 PUT status=inactive 軟下架。"""
    sc = split_code.strip()
    ref = db.collection("Products").document(sc)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail=f"找不到商品：{sc}")

    ref.delete()
    db.collection("ProductsCost").document(sc).delete()

    await load_all_products()
    logger.info(f"🗑️ admin 刪除商品 {sc}")
    return {"splitCode": sc, "deleted": True}


# ═══════════════════════════════════════════
# POST /api/admin/products/import  （Excel 批次匯入；前端用 SheetJS 解析後送 JSON）
# ═══════════════════════════════════════════
@router.post("/products/import")
async def import_products(
    body: ImportBody,
    _admin: dict = Depends(verify_admin),
) -> dict:
    """批次 upsert 商品：存在則合併、不存在則新建；附 cost 則算 quotes + 寫 ProductsCost。
    整批寫完才刷一次快取（避免每筆都 reload）。回傳每筆結果供前端顯示。"""
    created = updated = failed = 0
    results: list[dict] = []

    for idx, item in enumerate(body.products):
        sc = (item.splitCode or "").strip()
        if not sc:
            failed += 1
            results.append({"row": idx + 1, "splitCode": "", "status": "error", "error": "缺商品編號 splitCode"})
            continue
        try:
            data = _dump_clean(item)
            cost = data.pop("cost", None)
            data.pop("splitCode", None)
            if cost is not None:
                data["quotes"] = compute_all_quotes(cost)

            ref = db.collection("Products").document(sc)
            exists = ref.get().exists
            if not exists:
                data["splitCode"] = sc
                data.setdefault("status", "active")
                ref.set(data)
                created += 1
            else:
                if data:
                    ref.set(data, merge=True)
                updated += 1

            if cost is not None:
                db.collection("ProductsCost").document(sc).set({"splitCode": sc, "cost": cost})

            results.append({"row": idx + 1, "splitCode": sc, "status": "created" if not exists else "updated"})
        except Exception as e:  # noqa: BLE001
            failed += 1
            results.append({"row": idx + 1, "splitCode": sc, "status": "error", "error": str(e)})

    await load_all_products()
    logger.info(f"📥 admin 匯入商品：建立 {created}、更新 {updated}、失敗 {failed}")
    return {"total": len(body.products), "created": created, "updated": updated, "failed": failed, "results": results}
