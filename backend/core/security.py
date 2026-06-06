"""
core/security.py
────────────────
Firebase ID Token 驗證（給網頁後台 / 查價 API 用）。

前端登入後，呼叫後端 API 時於 Header 帶上：
    Authorization: Bearer <Firebase ID Token>

verify_token  → 驗證並回傳 decoded claims（含 uid / email / level）
verify_admin  → 在 verify_token 之上要求 level >= 4
"""

import logging

from fastapi import Header, HTTPException, Depends
from firebase_admin import auth as fb_auth

logger = logging.getLogger(__name__)


async def verify_token(authorization: str = Header(None)) -> dict:
    """驗證 Firebase ID Token，回傳 decoded token（dict）。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少身分憑證")

    id_token = authorization[len("Bearer "):].strip()
    try:
        return fb_auth.verify_id_token(id_token)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⚠️ ID token 驗證失敗: {e}")
        raise HTTPException(status_code=401, detail="身分憑證無效或過期")


async def verify_admin(claims: dict = Depends(verify_token)) -> dict:
    """要求管理員權限（level >= 4，來自 custom claims）。"""
    if int(claims.get("level", 0) or 0) < 4:
        raise HTTPException(status_code=403, detail="需要管理員權限")
    return claims
