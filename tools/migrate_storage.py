"""Migrate product images from old Firebase Storage to new bucket, and update Firestore imageUrl."""
import json
import os
import re
import uuid
import requests
import firebase_admin
from firebase_admin import credentials, firestore, storage

# Init with NEW service account
sa_path = os.path.join(os.path.dirname(__file__), "..", "backend", "serviceAccountKey.json")
cred = credentials.Certificate(sa_path)
app = firebase_admin.initialize_app(cred, {"storageBucket": "lingdong-price-tw.firebasestorage.app"})
db = firestore.client()
bucket = storage.bucket()

OLD_BUCKET = "lingdong-price.firebasestorage.app"
NEW_BUCKET = "lingdong-price-tw.firebasestorage.app"

# Read from exported JSON to avoid Firestore stream timeout
export_path = os.path.join(os.path.dirname(__file__), "..", "firestore_export", "Products.json")
with open(export_path, encoding="utf-8") as f:
    products = json.load(f)

migrated = 0
failed = 0
products_ref = db.collection("Products")

for product in products:
    doc_id = product.get("_id", "")
    image_url = product.get("imageUrl", "")
    if not image_url or OLD_BUCKET not in image_url:
        continue

    match = re.search(r"/o/(.+?)\?", image_url)
    if not match:
        print(f"  SKIP {doc_id}: can't parse URL")
        failed += 1
        continue

    encoded_path = match.group(1)
    storage_path = requests.utils.unquote(encoded_path)

    # Download from old URL
    try:
        resp = requests.get(image_url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  FAIL {doc_id}: download error - {e}")
        failed += 1
        continue

    # Upload to new bucket with download token
    try:
        blob = bucket.blob(storage_path)
        content_type = resp.headers.get("Content-Type", "image/gif")
        token = str(uuid.uuid4())
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        blob.upload_from_string(resp.content, content_type=content_type)
        encoded = requests.utils.quote(storage_path, safe="")
        new_url = f"https://firebasestorage.googleapis.com/v0/b/{NEW_BUCKET}/o/{encoded}?alt=media&token={token}"
    except Exception as e:
        print(f"  FAIL {doc_id}: upload error - {e}")
        failed += 1
        continue

    # Update Firestore
    products_ref.document(doc_id).update({"imageUrl": new_url})
    migrated += 1
    print(f"  OK {doc_id}: {storage_path}")

print(f"\nDone: {migrated} migrated, {failed} failed")
