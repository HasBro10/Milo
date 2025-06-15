import os
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Firebase setup
firebase_key_path = os.getenv('FIREBASE_KEY_PATH', 'firebase_key.json')
cred = credentials.Certificate(firebase_key_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Migration logic
def migrate_merchants():
    users_ref = db.collection('users')
    users = users_ref.stream()

    migrated = []

    for user in users:
        data = user.to_dict()
        if data.get('role') == 'merchant':
            merchant_id = user.id
            db.collection('merchants').document(merchant_id).set({
                'email': data.get('email'),
                'created_at': data.get('created_at'),
                'name': data.get('name', 'Unnamed Merchant')
            })
            users_ref.document(merchant_id).delete()
            migrated.append(merchant_id)

    print(f"Migrated {len(migrated)} merchants: {migrated}")

if __name__ == "__main__":
    migrate_merchants()
