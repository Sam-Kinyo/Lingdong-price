"""
routers/admin_api.py
────────────────────
帳號管理 API（管理員專用，level >= 4）。

供 Web 後台「帳號管理」面板呼叫：
  GET    /api/admin/users          列出所有帳號
  POST   /api/admin/users          建立帳號（Auth + custom claim level + 寫 Users 文件）
  PATCH  /api/admin/users/{email}  調整帳號等級
  DELETE /api/admin/users/{email}  刪除帳號

所有端點皆以 verify_admin 保護（需 Firebase ID token，且 token claim level >= 4）。
- 權限模型見 core/security.py
- claim 設定慣例同 tools/sync_user_claims.py（auth.set_custom_user_claims(uid, {"level": N})）
- Users 文件：doc id = email 小寫，欄位 { "level": int }
- ⚠️ 設/改 claim 後，該使用者需「重新登入」讓 ID token 刷新，新等級才會生效。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from firebase_admin import auth as fb_auth

from core.security import verify_admin
from core.config import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"])

# 有效等級 1~4（4 = 管理員；對應 pricing DIVISOR_MAP 與 verify_admin 門檻）
MIN_LEVEL, MAX_LEVEL = 1, 4


# ═══════════════════════════════════════════
# Request Models
# ═══════════════════════════════════════════
class CreateUserBody(BaseModel):
    email: str = Field(..., description="登入 email")
    password: str = Field(..., min_length=6, description="初始密碼（至少 6 碼）")
    level: int = Field(..., ge=MIN_LEVEL, le=MAX_LEVEL, description="權限等級 1~4")


class UpdateLevelBody(BaseModel):
    level: int = Field(..., ge=MIN_LEVEL, le=MAX_LEVEL, description="新權限等級 1~4")


# ═══════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════
def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _doc_level(email: str) -> int | None:
    """讀 Users/{email} 的 level（找不到回 None，供與 claim 比對）。"""
    doc = db.collection("Users").document(email).get()
    if doc.exists:
        return int((doc.to_dict() or {}).get("level", 0) or 0)
    return None


# ═══════════════════════════════════════════
# GET /api/admin/users
# ═══════════════════════════════════════════
@router.get("/users")
async def list_users(_admin: dict = Depends(verify_admin)) -> dict:
    """列出所有帳號，合併 Auth 資訊 + 生效中的 claim level + Users 文件 level。"""
    users: list[dict] = []
    for u in fb_auth.list_users().iterate_all():
        email = (u.email or "").strip()
        claim_level = int(((u.custom_claims or {}).get("level", 0)) or 0)
        users.append(
            {
                "uid": u.uid,
                "email": email,
                "level": claim_level,                       # 實際生效（token claim）
                "levelFromDoc": _doc_level(email.lower()),  # Users 文件（比對用）
                "disabled": u.disabled,
                "created": (u.user_metadata.creation_timestamp
                            if u.user_metadata else None),
            }
        )
    users.sort(key=lambda x: (-x["level"], x["email"]))
    logger.info(f"👥 admin 列出 {len(users)} 個帳號")
    return {"count": len(users), "users": users}


# ═══════════════════════════════════════════
# POST /api/admin/users
# ═══════════════════════════════════════════
@router.post("/users", status_code=201)
async def create_user(
    body: CreateUserBody,
    _admin: dict = Depends(verify_admin),
) -> dict:
    """建立帳號：Firebase Auth → 設 custom claim level → 寫 Users 文件。"""
    email = _norm_email(body.email)
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="email 格式不正確")

    # 1. 建 Auth 帳號
    try:
        user = fb_auth.create_user(email=email, password=body.password)
    except fb_auth.EmailAlreadyExistsError:
        raise HTTPException(status_code=409, detail=f"帳號已存在：{email}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"參數錯誤：{e}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"建立 Auth 帳號失敗: {e}")
        raise HTTPException(status_code=400, detail=f"建立帳號失敗：{e}")

    # 2. 設 custom claim level
    fb_auth.set_custom_user_claims(user.uid, {"level": body.level})

    # 3. 寫 Users 文件（doc id = email 小寫）
    db.collection("Users").document(email).set({"level": body.level})

    logger.info(f"➕ admin 建立帳號 {email} level={body.level}")
    return {
        "uid": user.uid,
        "email": email,
        "level": body.level,
        "note": "新帳號可直接登入；claim 在首次登入即生效",
    }


# ═══════════════════════════════════════════
# PATCH /api/admin/users/{email}
# ═══════════════════════════════════════════
@router.patch("/users/{email}")
async def update_user_level(
    body: UpdateLevelBody,
    email: str = Path(..., description="目標帳號 email"),
    admin: dict = Depends(verify_admin),
) -> dict:
    """調整帳號等級：更新 custom claim + Users 文件。"""
    email = _norm_email(email)

    # 防呆：不可把自己降到管理員等級以下（會自我鎖死後台）
    if _norm_email(admin.get("email", "")) == email and body.level < 4:
        raise HTTPException(status_code=400, detail="不可將自己降到管理員（level 4）以下")

    try:
        user = fb_auth.get_user_by_email(email)
    except fb_auth.UserNotFoundError:
        raise HTTPException(status_code=404, detail=f"找不到帳號：{email}")

    fb_auth.set_custom_user_claims(user.uid, {"level": body.level})
    db.collection("Users").document(email).set({"level": body.level}, merge=True)

    logger.info(f"✏️ admin 調整 {email} → level={body.level}")
    return {
        "uid": user.uid,
        "email": email,
        "level": body.level,
        "note": "該使用者需『重新登入』才會讓新等級生效",
    }


# ═══════════════════════════════════════════
# DELETE /api/admin/users/{email}
# ═══════════════════════════════════════════
@router.delete("/users/{email}")
async def delete_user(
    email: str = Path(..., description="目標帳號 email"),
    admin: dict = Depends(verify_admin),
) -> dict:
    """刪除帳號：移除 Firebase Auth 與 Users 文件。"""
    email = _norm_email(email)

    # 防呆：不可刪除自己
    if _norm_email(admin.get("email", "")) == email:
        raise HTTPException(status_code=400, detail="不可刪除自己的帳號")

    try:
        user = fb_auth.get_user_by_email(email)
    except fb_auth.UserNotFoundError:
        raise HTTPException(status_code=404, detail=f"找不到帳號：{email}")

    fb_auth.delete_user(user.uid)
    db.collection("Users").document(email).delete()

    logger.info(f"🗑️ admin 刪除帳號 {email}")
    return {"email": email, "deleted": True}
