"""Export all Firestore collections from lingdong-price project."""
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore

# Use the existing service account key
sa_path = os.path.join(os.path.dirname(__file__), "..", "backend", "serviceAccountKey.json")
cred = credentials.Certificate(sa_path)
app = firebase_admin.initialize_app(cred)
db = firestore.client()

export_dir = os.path.join(os.path.dirname(__file__), "..", "firestore_export")
os.makedirs(export_dir, exist_ok=True)

# List and export all collections
collections = db.collections()
total = 0

for col_ref in collections:
    col_name = col_ref.id
    docs = []
    for doc in col_ref.stream():
        doc_data = doc.to_dict()
        docs.append({"_id": doc.id, **doc_data})

    out_path = os.path.join(export_dir, f"{col_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2, default=str)

    print(f"  {col_name}: {len(docs)} docs -> {out_path}")
    total += len(docs)

print(f"\nTotal: {total} documents exported to {export_dir}")
