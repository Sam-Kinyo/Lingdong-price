"""
routers/catalog_api.py
──────────────────────
網頁查價 API。

回傳商品列表 + 依登入者等級算好的各級距報價，但「絕不包含成本價(cost)」。
成本價只存在後端記憶體快取，永不下發給前端 —— 客戶因此無法取得成本。

採白名單制：只有 PUBLIC_FIELDS 內的欄位會下發，其餘（含 cost、VIP 專屬價欄位）一律不送。
"""

import logging

from fastapi import APIRouter, Depends

from core.security import verify_token
from core.config import db
from database.firestore_db import ProductCache
from services.pricing_service import compute_tier_quotes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Catalog"])

# 可下發前端的公開欄位白名單（刻意不含 cost 與任何 VIP 價格欄位）
PUBLIC_FIELDS: tuple[str, ...] = (
    "splitCode", "brand", "category", "model", "mainModel", "name",
    "marketPrice", "minPrice", "status", "statusText", "isControlled",
    "internationalBarcode", "barcode", "cartonQty", "bsmi", "ncc",
    "productUrl", "netSalesPermission", "inventory", "eta", "colorList",
    "mainImage", "netImages", "imageUrl", "imageSource", "folderUrl",
    "driveMapping", "sourceSheet", "_doc_id",
)


def _resolve_level(claims: dict) -> int:
    """優先用 custom claims 的 level；沒有就退回讀 Firestore Users（相容尚未設 claims 的帳號）。"""
    level = int(claims.get("level", 0) or 0)
    if level > 0:
        return level
    email = str(claims.get("email", "")).strip().lower()
    if email:
        try:
            doc = db.collection("Users").document(email).get()
            if doc.exists:
                return int((doc.to_dict() or {}).get("level", 0) or 0)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"讀取 Users level 失敗: {e}")
    return level


@router.get("/catalog")
async def get_catalog(claims: dict = Depends(verify_token)) -> dict:
    """回傳「不含成本價」的商品清單，每筆附該登入者等級的各級距報價。"""
    level = _resolve_level(claims)
    products: list[dict] = []
    for p in ProductCache.get_all().values():
        safe = {k: p.get(k) for k in PUBLIC_FIELDS if k in p}
        safe["quotes"] = compute_tier_quotes(p.get("cost"), level)
        products.append(safe)
    logger.info(f"📦 /api/catalog → level={level}, {len(products)} products (no cost)")
    return {"level": level, "count": len(products), "products": products}
