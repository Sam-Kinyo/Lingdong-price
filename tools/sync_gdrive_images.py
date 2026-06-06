import os
import re
import io
import time
import uuid
import sys
import urllib.parse
from datetime import datetime

# Attempt to import necessary packages
try:
    import firebase_admin
    from firebase_admin import credentials, firestore, storage
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except ImportError as e:
    print(f"缺少必備套件: {e}")
    print("請先執行 pip install firebase-admin google-api-python-client google-auth-httplib2 google-auth-oauthlib")
    sys.exit(1)

TARGET_FOLDER_ID = "19cwRAlrqg_abz_obAffnD1qVO4ROgI-7"  # sam.kuo@lingdong.tw 的雲端硬碟圖庫（2026-06 由 kuo.tinghow 移轉）
# 已切換到新專案 lingdong-price-tw（舊專案 lingdong-price 已刪除）
FIREBASE_CREDENTIAL_FILE = os.path.join(os.path.dirname(__file__), "..", "backend", "serviceAccountKey.json")
DRIVE_CREDENTIAL_FILE = r"D:\SAM-KINYO-WEBSITE\kinyo-price\functions\credentials.json"
FIREBASE_BUCKET = "lingdong-price-tw.firebasestorage.app"

def get_drive_service():
    scopes = ['https://www.googleapis.com/auth/drive.readonly']
    creds = service_account.Credentials.from_service_account_file(DRIVE_CREDENTIAL_FILE, scopes=scopes)
    return build('drive', 'v3', credentials=creds)

def init_firebase():
    if not os.path.exists(FIREBASE_CREDENTIAL_FILE):
        print(f"[ERROR] 找不到 Firebase/GCP 憑證檔: {FIREBASE_CREDENTIAL_FILE}")
        sys.exit(1)
        
    cred = credentials.Certificate(FIREBASE_CREDENTIAL_FILE)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {'storageBucket': FIREBASE_BUCKET})
    return firestore.client(), storage.bucket()

def list_files(service, query):
    results = []
    page_token = None
    while True:
        resp = service.files().list(
            q=query, 
            fields='nextPageToken, files(id, name, mimeType)', 
            pageToken=page_token,
            pageSize=1000
        ).execute()
        results.extend(resp.get('files', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return results

def get_split_codes(filename: str):
    """從檔名解析出一或多個分流碼。

    支援多色合併檔名（分流碼以 - _ 空白 連接，例如 240A43-240A44-240A45），
    並去除「首圖/首/主圖」、「(數字)」、結尾編號等後綴。只回傳符合分流碼格式
    （3 位數字開頭的英數，例如 240463 / 240A43 / 525C16）的片段，避免把商品
    描述文字誤判為分流碼。"""
    name, _ = os.path.splitext(filename)
    # 去除常見後綴
    name = re.sub(r'[-_ ]*(首圖|主圖|首|主|main|cover)\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_ ]*\(\d+\)\s*$', '', name)
    # 不在此去除結尾「-數字」：會誤刪純數字分流碼（如 -525257）。
    # 短編號（如 -8）位數不足 3 位，會被下方格式過濾自然排除。

    codes = []
    for part in re.split(r'[-_ ]+', name):
        part = part.strip().upper()
        if part.startswith("WV") and re.match(r'^WV[A-Z0-9]+$', part):
            codes.append(part)
        elif re.match(r'^\d{3}[A-Z0-9]*$', part):
            codes.append(part)

    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out

def download_image(drive_service, file_id):
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    return fh.getvalue()

def get_firebase_url(bucket_name, dest, token):
    encoded_dest = urllib.parse.quote(dest, safe="")
    return f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{encoded_dest}?alt=media&token={token}"

def upload_firebase(bucket, img_id, filename, model_key, binary_data, content_type):
    ext = os.path.splitext(filename)[1].lower() or '.jpg'
    dest = f"product_images/{model_key}/{img_id}{ext}"
    blob = bucket.blob(dest)
    
    # 如果已經存在，直接抓 URL 和 Token 就不需要重新上傳
    if blob.exists():
        metadata = blob.metadata or {}
        token = metadata.get('firebaseStorageDownloadTokens')
        if not token:
            token = str(uuid.uuid4())
            blob.metadata = {'firebaseStorageDownloadTokens': token}
            blob.patch()
        return get_firebase_url(bucket.name, dest, token)
    
    token = str(uuid.uuid4())
    blob.metadata = {'firebaseStorageDownloadTokens': token}
    blob.cache_control = 'public, max-age=31536000'
    blob.upload_from_string(binary_data, content_type=content_type)
    return get_firebase_url(bucket.name, dest, token)

def scan_new_structure_folder(drive_service, db, bucket, folder_id, folder_name):
    print(f"\n📁 掃描目錄: {folder_name} ({folder_id})")
    items = list_files(drive_service, f"'{folder_id}' in parents and trashed=false")
    
    # 直接層圖片 = 主圖候選
    images = [img for img in items if img.get('mimeType', '').startswith('image/')]
    # 併入「網路圖」子資料夾的圖，標記為網路圖（PPT 下排的 netImages）
    for _sub in items:
        if _sub.get('mimeType') == 'application/vnd.google-apps.folder' and ('網路' in _sub['name'] or 'net' in _sub['name'].lower()):
            for _ni in list_files(drive_service, f"'{_sub['id']}' in parents and trashed=false"):
                if _ni.get('mimeType', '').startswith('image/'):
                    _ni['_isNet'] = True
                    images.append(_ni)
    if not images:
        print("   -> 無圖片檔案。")
        return
        
    # 分析所有圖片並依照「分流(Split Code)」分組
    group_by_model = {}
    for img in images:
        for model_key in get_split_codes(img['name']):
            group_by_model.setdefault(model_key, []).append(img)
        
    for model_key, group_images in group_by_model.items():
        print(f" └─> 🔍 發現分流 [{model_key}] 共 {len(group_images)} 張圖片，開始同步...")
        
        # 按照檔名排序確保序號正確
        group_images.sort(key=lambda x: x['name'])
        
        doc_ref = db.collection('Products').document(model_key)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            print(f"   ⚠️ 略過 [{model_key}]：資料庫無此分流碼，不創建幽靈")
            continue
        doc_data = doc_snap.to_dict()
        
        drive_mapping = doc_data.get('driveMapping', {})
        net_images_urls = []
        main_image_url = ""
        _directs = [im for im in group_images if not im.get('_isNet')]
        main_img_obj = next((im for im in _directs if '首' in im['name']), _directs[0] if _directs else None)
        
        uploaded = 0
        for img in group_images:
            if img['id'] not in drive_mapping:
                try:
                    print(f"      📥 下載中: {img['name']} ...", end="", flush=True)
                    binary = download_image(drive_service, img['id'])
                    url = upload_firebase(bucket, img['id'], img['name'], model_key, binary, img['mimeType'])
                    drive_mapping[img['id']] = url
                    uploaded += 1
                    print(" 完成!")
                except Exception as e:
                    print(f" 失敗! ({e})")
                    continue
            
            url = drive_mapping.get(img['id'])
            if not url: continue
            
            # 主圖 = 直接層主圖；網路圖 = 「網路圖」子資料夾的圖
            if img == main_img_obj:
                main_image_url = url
            elif img.get('_isNet'):
                if url not in net_images_urls:
                    net_images_urls.append(url)
                    
        # 若未標示首圖，自動拿網路圖第一張當首圖防呆
        if not main_image_url and net_images_urls:
            main_image_url = net_images_urls[0]
            
        updates = {
            "mainImage": main_image_url,
            "netImages": net_images_urls,
            "driveMapping": drive_mapping,
            "imageSource": "google_drive",
            "imageUpdatedAt": firestore.SERVER_TIMESTAMP,
            "folderUrl": f"https://drive.google.com/drive/folders/{folder_id}"
        }
        
        doc_ref.set(updates, merge=True)
        if uploaded > 0:
            print(f"     ✅ 分流 [{model_key}] 資料庫已更新！")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只處理前 N 個資料夾（0=全部），測試用")
    args = ap.parse_args()

    print("🚀 LingDong 圖片圖庫同步啟動中...")
    db, bucket = init_firebase()
    drive_service = get_drive_service()
    
    print(f"🔍 掃描根目錄: {TARGET_FOLDER_ID}")
    root_items = list_files(drive_service, f"'{TARGET_FOLDER_ID}' in parents and trashed=false")
    root_items.sort(key=lambda x: x['name'])
    
    print(f"共找到 {len(root_items)} 個項目。")
    count = 0
    folder_items = [i for i in root_items if i.get('mimeType') == 'application/vnd.google-apps.folder']
    if args.limit:
        folder_items = folder_items[:args.limit]
        print(f"[測試模式] 只處理前 {len(folder_items)} 個資料夾")
    for item in folder_items:
        count += 1
        print(f"⏳ 進度: {count} / {len(root_items)}")
        
        # 我們只處理資料夾
        if item.get('mimeType') == 'application/vnd.google-apps.folder':
            for attempt in range(3):
                try:
                    scan_new_structure_folder(drive_service, db, bucket, item['id'], item['name'])
                    break
                except Exception as e:
                    print(f"   ⚠️ 網路出錯，重試 {attempt+1}/3: {type(e).__name__}")
                    time.sleep(3)
            else:
                print(f"   ❌ 重試 3 次仍失敗，跳過此資料夾: {item['name']}")
            
    print("\n🎉 全部圖庫同步完成！")

if __name__ == '__main__':
    main()
