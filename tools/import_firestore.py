"""Import exported Firestore data into the new lingdong-price-tw project."""
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore

# Use the NEW service account key
sa_path = os.path.join(os.path.dirname(__file__), "..", "backend", "serviceAccountKey.json")
cred = credentials.Certificate(sa_path)
app = firebase_admin.initialize_app(cred)
db = firestore.client()

export_dir = os.path.join(os.path.dirname(__file__), "..", "firestore_export")

# Import each collection
for filename in os.listdir(export_dir):
    if not filename.endswith(".json"):
        continue
    col_name = filename.replace(".json", "")
    filepath = os.path.join(export_dir, filename)

    with open(filepath, "r", encoding="utf-8") as f:
        docs = json.load(f)

    batch = db.batch()
    count = 0
    for doc_data in docs:
        doc_id = doc_data.pop("_id")
        ref = db.collection(col_name).document(doc_id)
        batch.set(ref, doc_data)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
            print(f"  {col_name}: committed {count} docs...")

    if count % 400 != 0:
        batch.commit()

    print(f"  {col_name}: {count} docs imported")

print("\nAll collections imported successfully!")
