import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash
import firebase_admin
from firebase_admin import credentials, firestore, auth, storage
import datetime
from werkzeug.utils import secure_filename
import openai
import fitz  # PyMuPDF
import csv
import json
import pytz

# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Firebase setup
firebase_key_path = os.getenv('FIREBASE_KEY_PATH', 'firebase_key.json')
cred = credentials.Certificate(firebase_key_path)
firebase_admin.initialize_app(cred, {
    'storageBucket': 'chat2order-632e9.appspot.com'
})
db = firestore.client()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        role = request.form.get('role', 'customer').lower()
        allowed_roles = ['customer', 'merchant', 'admin']
        if role not in allowed_roles:
            role = 'customer'

        try:
            user = auth.create_user(email=email, password=password)

            if role == 'merchant':
                data = {
                    'email': email,
                    'role': role,
                    'created_at': datetime.datetime.utcnow(),
                    'name': request.form.get('name', '')
                }
                db.collection('merchants').document(user.uid).set(data)
            else:
                data = {
                    'email': email,
                    'role': role,
                    'created_at': datetime.datetime.utcnow()
                }
                db.collection('users').document(user.uid).set(data)

            session.clear()
            flash('Signup successful. Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(str(e), 'danger')

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user_ref = db.collection('users').where('email', '==', email).stream()
        user = next(user_ref, None)
        collection_used = 'users'

        if not user:
            merchant_ref = db.collection('merchants').where('email', '==', email).stream()
            user = next(merchant_ref, None)
            collection_used = 'merchants'

        if user:
            user_data = user.to_dict()
            session['user'] = {
                'id': user.id,
                'email': user_data.get('email', ''),
                'role': user_data.get('role', 'customer')
            }
            flash(f'Login successful from {collection_used}.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('User not found.', 'danger')

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash('Please log in to access the dashboard.')
        return redirect(url_for('login'))

    user_id = session['user']['id']
    role = session['user'].get('role', 'customer')
    orders = []
    merchants = []
    menu_items = []

    user_data = {}
    if role == 'merchant':
        user_doc = db.collection('merchants').document(user_id).get()
    else:
        user_doc = db.collection('users').document(user_id).get()
    if user_doc.exists:
        user_data = user_doc.to_dict()

    if role == 'admin':
        orders = [doc.to_dict() | {'id': doc.id} for doc in db.collection('orders').stream()]
        merchants = [doc.to_dict() | {'id': doc.id} for doc in db.collection('merchants').stream()]

    elif role == 'merchant':
        from datetime import datetime
        import pytz

        merchant_ref = db.collection('merchants').document(user_id)
        if merchant_ref.get().exists:
            orders_query = db.collection('orders').where('merchant_id', '==', user_id).stream()
            orders = []
            today_order_count = 0
            today_earnings = 0

            london = pytz.timezone('Europe/London')
            now = datetime.now(london)

            for doc in orders_query:
                data = doc.to_dict()
                data['id'] = doc.id
                orders.append(data)

                # Use created_at datetime instead of created_at_str
                created_at = data.get('created_at')
                if created_at:
                    order_time = created_at.astimezone(london)
                    if order_time.date() == now.date():
                        today_order_count += 1
                        try:
                            today_earnings += float(data.get('total', 0))
                        except:
                            pass

            menu_ref = merchant_ref.collection('menu')
            menu_docs = menu_ref.order_by('position').stream()
            menu_items = [{**doc.to_dict(), 'id': doc.id} for doc in menu_docs]
        else:
            flash('Merchant profile not found.')

    elif role == 'customer':
        orders = [doc.to_dict() | {'id': doc.id} for doc in db.collection('orders').where('customer_id', '==', user_id).stream()]

    return render_template(
        'dashboard.html',
        orders=orders,
        merchants=merchants,
        menu_items=menu_items,
        user=user_data,
        today_order_count=today_order_count if role == 'merchant' else 0,
        today_earnings=today_earnings if role == 'merchant' else 0
    )

@app.route('/add_menu_item', methods=['POST'])
def add_menu_item():
    if 'user' not in session or session['user']['role'] != 'merchant':
        return redirect(url_for('login'))

    user_id = session['user']['id']
    category = request.form.get('category', '').strip()
    name = request.form.get('name', '').strip()
    price = float(request.form.get('price', 0))
    description = request.form.get('description', '').strip()
    option = request.form.get('option', '').strip()
    image = request.files.get('image')

    image_url = None
    if image and image.filename != '':
        filename = secure_filename(image.filename)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        storage_path = f'menu_images/{user_id}/{timestamp}_{filename}'

        bucket = firebase_admin.storage.bucket()
        blob = bucket.blob(storage_path)
        blob.upload_from_file(image.stream, content_type=image.content_type)
        image_url = blob.public_url

    item_data = {
        'name': name,
        'price': price,
        'description': description,
        'option': option,
        'category': category,
        'position': int(datetime.datetime.utcnow().timestamp())
        'image_url': image_url
    }

    db.collection('merchants').document(user_id).collection('menu').add(item_data)
    flash('Menu item added successfully.')
    return redirect(url_for('dashboard'))


@app.route('/delete_menu_item/<item_id>', methods=['POST'])
def delete_menu_item(item_id):
    if 'user' not in session or session['user']['role'] != 'merchant':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    user_id = session['user']['id']
    try:
        menu_ref = db.collection('merchants').document(user_id).collection('menu')
        menu_ref.document(item_id).delete()
        flash('Menu item deleted.', 'success')
    except Exception as e:
        flash(f'Error deleting item: {e}', 'danger')

    return redirect(url_for('dashboard'))

@app.route('/edit_menu_item/<item_id>', methods=['POST'])
def edit_menu_item(item_id):
    if 'user' not in session or session['user']['role'] != 'merchant':
        return redirect(url_for('login'))

    name = request.form.get('name')
    price = float(request.form.get('price', 0))
    description = request.form.get('description', '')
    option = request.form.get('option', '')

    user_id = session['user']['id']
    item_ref = db.collection('merchants').document(user_id).collection('menu').document(item_id)
    item_ref.update({
        'name': name,
        'price': price,
        'description': description,
        'option': option
    })

    flash('Menu item updated.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/move_menu_item/<item_id>', methods=['POST'])
def move_menu_item(item_id):
    user = session.get('user')
    if not user or user.get('role') != 'merchant':
        return redirect(url_for('login'))

    direction = request.form.get('direction')
    merchant_id = user['id']

    # Find item
    item_ref = db.collection('merchants').document(merchant_id).collection('menu').document(item_id)
    item = item_ref.get()
    if not item.exists:
        flash("Menu item not found.")
        return redirect(url_for('dashboard'))

    item_data = item.to_dict()
    category = item_data.get('category')
    position = item_data.get('position', 0)

    # Fetch all items in the same category
    items_query = db.collection('merchants').document(merchant_id).collection('menu')\
        .where('category', '==', category).order_by('position')
    items = items_query.stream()

    sorted_items = sorted([i for i in items], key=lambda x: x.to_dict().get('position', 0))
    item_ids = [i.id for i in sorted_items]

    index = item_ids.index(item_id)
    new_index = index - 1 if direction == 'up' else index + 1

    if 0 <= new_index < len(item_ids):
        other_item_id = item_ids[new_index]
        other_item_ref = db.collection('merchants').document(merchant_id).collection('menu').document(other_item_id)

        this_pos = item_data.get('position', 0)
        other_pos = other_item_ref.get().to_dict().get('position', 0)

        item_ref.update({'position': other_pos})
        other_item_ref.update({'position': this_pos})

    return redirect(url_for('dashboard'))

@app.route('/mark_paid/<order_id>', methods=['POST'])
def mark_paid(order_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    try:
        db.collection('orders').document(order_id).update({'status': 'Paid'})
        flash('Order marked as paid.', 'success')
    except Exception as e:
        flash(str(e), 'danger')

    return redirect(url_for('dashboard'))

@app.route('/place_order')
def place_order():
    if 'user' not in session or session['user'].get('role') == 'merchant':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    reorder = None
    merchants = []
    selected_merchant = None
    cart = session.get('cart', [])
    menu_items = []

    # Check if reorder is active
    reorder_id = session.get('reorder_from')
    if reorder_id:
        order_doc = db.collection('orders').document(reorder_id).get()
        if order_doc.exists:
            reorder = order_doc.to_dict()
            reorder['order_id'] = reorder_id
            reorder['merchant_name'] = reorder.get('merchant_name', 'Selected Merchant')
            selected_merchant = reorder.get('merchant_id')

            # Load merchant's menu
            menu_ref = db.collection('merchants').document(selected_merchant).collection('menu').stream()
            for item in menu_ref:
                data = item.to_dict()
                menu_items.append({
                    'name': data.get('name'),
                    'price': data.get('price'),
                    'description': data.get('description'),
                    'option': data.get('option'),
                    'category': data.get('category', 'Uncategorized')
                })

        # ✅ Don’t load merchants dropdown during reorder
        return render_template(
            'place_order.html',
            merchants=[],
            cart=cart,
            menu=menu_items,
            selected_merchant=selected_merchant,
            reorder=reorder
        )

    # 🟢 Normal flow (not reorder)
    merchant_id = request.args.get('merchant_id')
    selected_merchant = merchant_id

    merchants_ref = db.collection('merchants').stream()
    merchants = [{
        'id': m.id,
        'name': m.to_dict().get('name') or m.to_dict().get('email') or 'Unnamed Merchant'
    } for m in merchants_ref]

    if merchant_id:
        menu_ref = db.collection('merchants').document(merchant_id).collection('menu').stream()
        for item in menu_ref:
            data = item.to_dict()
            menu_items.append({
                'name': data.get('name'),
                'price': data.get('price'),
                'description': data.get('description'),
                'option': data.get('option'),
                'category': data.get('category', 'Uncategorized')
            })

    return render_template(
        'place_order.html',
        merchants=merchants,
        cart=cart,
        menu=menu_items,
        selected_merchant=selected_merchant,
        reorder=None
    )

@app.route('/order/<order_id>')
def view_order(order_id):
    if 'user' not in session:
        flash('Please log in to view order details.', 'danger')
        return redirect(url_for('login'))

    # Fetch order by ID
    order_ref = db.collection('orders').document(order_id).get()
    if not order_ref.exists:
        flash('Order not found.', 'danger')
        return redirect(url_for('dashboard'))

    order_data = order_ref.to_dict()
    order_data['id'] = order_id

    return render_template('order_details.html', order=order_data)

@app.route('/confirm_order')
def confirm_order():
    if 'user' not in session or session['user'].get('role') == 'merchant':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('confirm_order.html')

@app.route('/submit_order', methods=['POST'])
def submit_order():
    if 'user' not in session or session['user'].get('role') == 'merchant':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    name = request.form.get('name')
    phone = request.form.get('phone')
    address = request.form.get('address')
    raw_items = request.form.get('items', '')
    total = request.form.get('total')
    merchant_id = request.form.get('merchant_id')
    merchant_name = "Unknown"
    merchant_image = ""
    if merchant_id:
        merchant_doc = db.collection('merchants').document(merchant_id).get()
        if merchant_doc.exists:
            merchant_data = merchant_doc.to_dict()
            merchant_name = merchant_data.get('name', 'Unknown')
            merchant_image = merchant_data.get('image', '')

    customer_id = session['user']['id'] if 'user' in session else None

    # Convert raw_items text to list of dicts
    items = []
    for line in raw_items.split('\n'):
        line = line.strip()
        if ' x' in line:
            name_qty = line.rsplit(' x', 1)
            if len(name_qty) == 2 and name_qty[1].isdigit():
                items.append({'name': name_qty[0].strip(), 'quantity': int(name_qty[1])})

    # Fallback if parsing fails
    if not items:
        flash('Could not process items. Please use format like: burger x2', 'danger')
        return redirect(url_for('place_order'))

    if not merchant_id or not customer_id:
        return "Merchant ID and customer ID required", 400

    # Create order ID
    counter_ref = db.collection('orders').document('meta')
    counter_doc = counter_ref.get()
    total_count = counter_doc.to_dict().get('total_count', 0) + 1 if counter_doc.exists else 1
    counter_ref.set({'total_count': total_count})

    import pytz
    now = datetime.datetime.now(pytz.timezone('Europe/London'))
    date_str = now.strftime("%d%m%Y-%H%M")
    custom_order_id = f"{date_str}-{str(total_count).zfill(5)}"

    print("DEBUG --- Local time (Europe/London):", now)

    order_data = {
        'name': name,
        'phone': phone,
        'address': address,
        'items': items,
        'total': float(total) if total else 0,
        'merchant_id': merchant_id,
        'merchant_name': merchant_name,
        'merchant_image': merchant_image,
        'customer_id': customer_id,
        'status': 'Pending',
        'created_at': now,
        'created_at_str': now.strftime('%d %b %Y %H:%M'),
        'timestamp_check': now,
        'custom_id': custom_order_id
    }


    db.collection('orders').document(custom_order_id).set(order_data)
    return render_template('order_success.html', name=name)


@app.route('/update_order_status/<order_id>', methods=['POST'])
def update_order_status(order_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    new_status = request.form.get('status')  # this matches the form field name in HTML
    if not new_status:
        flash('No status provided.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        db.collection('orders').document(order_id).update({'status': new_status})
        flash(f'Order updated to {new_status}.', 'success')
    except Exception as e:
        flash(str(e), 'danger')

    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/upload_menu', methods=['GET', 'POST'])
def upload_menu():
    if 'user' not in session or session['user'].get('role') != 'merchant':
        flash('Access denied. Merchants only.')
        return redirect(url_for('dashboard'))

    extracted_menu = None

    if request.method == 'POST':
        file = request.files.get('file')  # ✅ fixed: was 'menu_file'
        if not file:
            flash('No file uploaded.')
            return redirect(url_for('upload_menu'))

        filename = secure_filename(file.filename)
        filepath = os.path.join('uploads', filename)
        os.makedirs('uploads', exist_ok=True)
        file.save(filepath)

        ext = filename.lower().split('.')[-1]
        try:
            if ext == 'csv':
                extracted_menu = []
                reorder = session.pop('reorder_info', None)
                with open(filepath, 'r', newline='', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        extracted_menu.append(row)
            else:
                if ext == 'pdf':
                    doc = fitz.open(filepath)
                    text = ""
                    for page in doc:
                        text += page.get_text()
                    doc.close()
                elif ext in ['jpg', 'jpeg', 'png']:
                    from PIL import Image
                    import pytesseract
                    image = Image.open(filepath)
                    text = pytesseract.image_to_string(image)
                else:
                    flash('Unsupported file type.')
                    return redirect(url_for('upload_menu'))

                mpt = f"""Here is a menu. Extract the items and group them by category.

                For each item, return:
                - name
                - price (as a number, no currency symbol)
                - description (optional)
                - option (optional, for things like “Choose sandwich or baguette”)
                - category (section heading)
                - position (use order of appearance in the list)

                If a field is blank or just a dash, leave it empty.

                Return output as JSON list of items.
                Here is the menu:

                {text.strip()}
                """.strip()

                response = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": mpt}],
                    temperature=0.2
                )
                print("OpenAI response object:", response)
                raw = response['choices'][0]['message']['content']

                try:
                    items_raw = json.loads(raw)
                except Exception as e:
                    print("Error loading JSON:", e)
                    items_raw = []

                def clean_field(val):
                    if not val:
                        return ''
                    val = str(val).strip()
                    return '' if val in ['-', '—', 'None'] else val

                extracted_menu = []
                position_counter = 1

                for item in items_raw:
                    extracted_menu.append({
                        'name': clean_field(item.get('name')),
                        'category': clean_field(item.get('category')),
                        'price': float(item.get('price', 0)) if item.get('price') else 0,
                        'description': clean_field(item.get('description')),
                        'option': clean_field(item.get('option')),
                        'position': position_counter,
                        'type': 'menu_item',
                    })
                    position_counter += 1

                user_id = session['user']['id']
                menu_ref = db.collection('merchants').document(user_id).collection('menu')

                for doc in menu_ref.stream():
                    doc.reference.delete()

                for item in extracted_menu:
                    menu_ref.add(item)

        except Exception as e:
            flash(f"Error processing menu: {e}")
            return redirect(url_for('upload_menu'))

        return render_template('upload_menu.html', menu_items=extracted_menu)

    return render_template('upload_menu.html', menu_items=None)


@app.route('/orders')
def orders():
    if 'user' not in session:
        return redirect(url_for('login'))

    user = session['user']
    user_id = user.get('id')
    user_role = user.get('role', 'customer')

    orders_ref = db.collection('orders')

    # 🔍 Filter if merchant
    if user_role == 'merchant':
        orders_query = orders_ref.where('merchant_id', '==', user_id)
    else:
        orders_query = orders_ref

    docs = orders_query.order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    orders = []

    for doc in docs:
        order = doc.to_dict()
        order['order_id'] = doc.id

        # Format created date
        created = order.get('created_at') or order.get('timestamp_check')
        if isinstance(created, datetime.datetime):
            order['created'] = created.strftime("%d %b %Y %H:%M")
        else:
            order['created'] = created

        # Always show merchant_name if it exists in the order
        order['merchant_name'] = order.get('merchant_name', '—')

        orders.append(order)


    return render_template('orders.html', user=user, orders=orders)

@app.route('/reorder/<order_id>', methods=['GET'])
def reorder(order_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    order_ref = db.collection('orders').document(order_id)
    order = order_ref.get()
    if not order.exists:
        flash('Order not found.', 'danger')
        return redirect(url_for('dashboard'))

    order_data = order.to_dict()

    session['cart'] = order_data.get('items', [])
    session['reorder_info'] = {
        'merchant_id': order_data.get('merchant_id'),
        'merchant_name': order_data.get('merchant_name')
    }

    return redirect(url_for('place_order'))

@app.route('/clear_cart')
def clear_cart():
    session.pop('cart', None)
    session.pop('reorder_from', None)
    return redirect(url_for('place_order'))

if __name__ == '__main__':
    app.run(debug=True)
