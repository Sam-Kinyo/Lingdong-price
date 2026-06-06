import os
import re
import io
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

TARGET_FOLDER_ID = "16Oo0mn_MiMGRXuEfY9J6cTADNU3qO7F5"
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

def get_split_code(filename: str) -> str:
    # 1. 去除副檔名
    name, _ = os.path.splitext(filename)
    
    # 2. 清除不必要的後綴: "-首圖", "_09", " (9)"
    name = re.sub(r'[-_ ]*首圖$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_ ]*\(\d+\)$', '', name)
    name = re.sub(r'[-_]\d+$', '', name)
    
    # 3. 切割底線、空白
    parts = re.split(r'[_ ]', name)
    base = parts[0].upper()
    
    # 特例：WV- 開頭
    if base.startswith("WV-"):
        match_wv = re.match(r'^(WV-[A-Z0-9]+)([A-Z]*)$', base)
        if match_wv:
            return match_wv.group(1)
        return base
        
    # 一般拆分 (英文字+數字 或純數字)
    clean_base = base.replace('-', '')
    match = re.match(r'^([A-Z]*)(\d+)([A-Z]*)$', clean_base)
    if match:
        # e.g. "528851" -> group(1)="", group(2)="528851" -> returns "528851"
        return match.group(1) + match.group(2)
        
    return clean_base

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
    
    # 過濾出圖片檔案
    images = [img for img in items if img.get('mimeType', '').startswith('image/')]
    if not images:
        print("   -> 無圖片檔案。")
        return
        
    # 分析所有圖片並依照「分流(Split Code)」分組
    group_by_model = {}
    for img in images:
        model_key = get_split_code(img['name'])
        if not model_key:
            continue
        if model_key not in group_by_model:
            group_by_model[model_key] = []
        group_by_model[model_key].append(img)
        
    for model_key, group_images in group_by_model.items():
        print(f" └─> 🔍 發現分流 [{model_key}] 共 {len(group_images)} 張圖片，開始同步...")
        
        # 按照檔名排序確保序號正確
        group_images.sort(key=lambda x: x['name'])
        
        doc_ref = db.collection('Products').document(model_key)
        doc_snap = doc_ref.get()
        doc_data = doc_snap.to_dict() if doc_snap.exists else {}
        
        drive_mapping = doc_data.get('driveMapping', {})
        net_images_urls = []
        main_image_url = ""
        main_img_obj = next((img for img in group_images if '首圖' in img['name']), None)
        
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
            
            # 分離首圖與網路圖 (不把首圖混入網路圖陣列)
            if img == main_img_obj:
                main_image_url = url
            elif '首圖' not in img['name']:
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
            scan_new_structure_folder(drive_service, db, bucket, item['id'], item['name'])
            
    print("\n🎉 全部圖庫同步完成！")

if __name__ == '__main__':
    main()
