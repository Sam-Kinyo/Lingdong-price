"""
把 Firestore Users.level 同步成 Firebase Auth custom claims。

Firestore rules 與後端 API 都改用 token 內的 claim（request.auth.token.level）判斷權限，
比前端讀 Users.level 更安全（前端無法竄改）。

用法：
    python tools/sync_user_claims.py          # 同步所有帳號
    python tools/sync_user_claims.py --email someone@x.com --level 3   # 指定單一帳號

注意：設定 claim 後，該使用者需「重新登入」讓 ID token 刷新，claim 才會生效。
"""
import argparse

import firebase_admin
from firebase_admin import credentials, firestore, auth

cred = credentials.Certificate(
    r"D:\LINGDONG_PROJECT\lingdong-price\backend\serviceAccountKey.json"
)
firebase_admin.initialize_app(cred)
db = firestore.client()


def level_from_users(user) -> int:
    """從 Firestore Users 找該帳號的 level（key 可能是 email 小寫 / 原始 email / uid）。"""
    email = (user.email or "").strip()
    for key in [email.lower(), email, user.uid]:
        if not key:
            continue
        doc = db.collection("Users").document(key).get()
        if doc.exists:
            return int((doc.to_dict() or {}).get("level", 0) or 0)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", help="只處理這個 email")
    ap.add_argument("--level", type=int, help="直接指定 level（搭配 --email）")
    args = ap.parse_args()

    if args.email and args.level is not None:
        u = auth.get_user_by_email(args.email)
        auth.set_custom_user_claims(u.uid, {"level": args.level})
        print(f"  {args.email} → claim level={args.level}")
    else:
        n = 0
        for u in auth.list_users().iterate_all():
            level = level_from_users(u)
            auth.set_custom_user_claims(u.uid, {"level": level})
            print(f"  {u.email} → claim level={level}")
            n += 1
        print(f"完成，同步 {n} 個帳號")

    print("\n=== 驗證（重新讀取 claims）===")
    for u in auth.list_users().iterate_all():
        fresh = auth.get_user(u.uid)
        print(f"  {u.email}: custom_claims={fresh.custom_claims}")
    print("\n⚠️ 已設定的使用者需『重新登入』才會讓 claim 生效。")


if __name__ == "__main__":
    main()
