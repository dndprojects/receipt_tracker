import os
import io
import re
import base64
import imgkit
import subprocess
import datetime
import uuid
import hashlib 
import random 

import pytesseract
from PIL import Image

from flask import Flask, render_template, request, redirect, url_for, g, flash
from flask_sqlalchemy import SQLAlchemy
from flask_babel import Babel, _
from sqlalchemy import extract, desc, func

# --- App Config ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///box_distribution.db'
app.config['SECRET_KEY'] = 'your_secret_key_change_this'
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations' 

# --- Path to Mudslide Executable ---
MUDSLIDE_PATH = '/usr/bin/mudslide-linuxstatic-x64' # Using absolute path

# --- Database & Babel Init ---
db = SQLAlchemy(app)

def get_locale():
    return 'he' 

babel = Babel(app, locale_selector=get_locale)

@app.context_processor
def inject_locale():
    return dict(get_locale=get_locale)

# --- Database Models (FIXED & UPDATED) ---

class Store(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    phone = db.Column(db.String(20), nullable=True) 
    address = db.Column(db.String(200), nullable=True)
    keywords = db.Column(db.String(500), nullable=True) 
    deliveries = db.relationship('Delivery', backref='store', lazy=True)

class BoxType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type_name = db.Column(db.String(100), nullable=False, unique=True)
    keywords = db.Column(db.String(500), nullable=True) 
    # This relationship now works because box_type_id exists in Delivery
    deliveries = db.relationship('Delivery', backref='box_type', lazy=True) 

class Delivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('store.id'), nullable=False)
    
    # --- THIS IS THE FIX ---
    # Added box_type_id back, as it was in your original file
    box_type_id = db.Column(db.Integer, db.ForeignKey('box_type.id'), nullable=False)
    
    date = db.Column(db.Date, nullable=False) 
    
    # --- NEW FIELDS based on receipt image ---
    receipt_number = db.Column(db.Integer, unique=True, nullable=True)
    delivery_content = db.Column(db.String(500), nullable=True) # פירוט (טקסט חופשי)
    quantity_carton = db.Column(db.Integer, nullable=True, default=0)
    quantity_basket = db.Column(db.Integer, nullable=True, default=0)
    vehicle_number = db.Column(db.String(20), nullable=True)
    exit_time = db.Column(db.String(10), nullable=True)
    generated_image_path = db.Column(db.String(255), nullable=True)
    

# Create tables
with app.app_context():
    db.create_all()

# --- HELPER FUNCTION ---
def normalize_phone_number(phone_str):
    if not phone_str:
        return None
    digits = re.sub(r'\D', '', phone_str)
    if digits.startswith('972'):
        return digits
    elif digits.startswith('05'):
        return f"972{digits[1:]}"
    elif len(digits) == 9 and digits.startswith('5'):
        return f"972{digits}"
    else:
        print(f"Warning: Could not normalize phone number: {phone_str}")
        return None

# --- Main Routes ---
@app.route('/')
def index():
    # Fixed query to join Store and BoxType
    deliveries = Delivery.query.join(Store, Delivery.store_id == Store.id)\
                               .join(BoxType, Delivery.box_type_id == BoxType.id)\
                               .order_by(desc(Delivery.date)).all()
    return render_template('index.html', deliveries=deliveries)

@app.route('/add_delivery', methods=['GET', 'POST'])
def add_delivery():
    if request.method == 'POST':
        date_obj = datetime.datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        
        delivery = Delivery(
            store_id = request.form['store_id'],
            box_type_id = request.form['box_type_id'], # <-- Added this back
            date = date_obj,
            delivery_content = request.form.get('delivery_content'),
            quantity_carton = request.form.get('quantity_carton') or 0,
            quantity_basket = request.form.get('quantity_basket') or 0,
            vehicle_number = request.form.get('vehicle_number'),
            exit_time = request.form.get('exit_time')
        )
        
        db.session.add(delivery)
        db.session.commit()
        
        try:
            generate_and_send_receipt(delivery)
            flash('Delivery added and receipt sent!', 'success')
        except Exception as e:
            print(f"Error generating/sending receipt: {e}")
            flash(f'Error generating receipt: {e}', 'danger')
            
        return redirect(url_for('index'))
    
    # GET request
    stores = Store.query.all()
    box_types = BoxType.query.all() # <-- Pass box_types to the template
    today_date_str = datetime.date.today().strftime('%Y-%m-%d')
    return render_template('add_delivery.html', stores=stores, box_types=box_types, today_date=today_date_str)

@app.route('/add_store', methods=['GET', 'POST'])
def add_store():
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form.get('phone')
        address = request.form.get('address')
        keywords = request.form.get('keywords')
        store = Store(name=name, phone=phone, address=address, keywords=keywords)
        db.session.add(store)
        db.session.commit()
        flash(f'Store {name} added!', 'success')
        return redirect(url_for('index'))
    return render_template('add_store.html')

@app.route('/add_box_type', methods=['GET', 'POST'])
def add_box_type():
    if request.method == 'POST':
        type_name = request.form['type_name']
        keywords = request.form.get('keywords')
        box_type = BoxType(type_name=type_name, keywords=keywords)
        db.session.add(box_type)
        db.session.commit()
        flash('Box type added!', 'success')
        return redirect(url_for('index'))
    return render_template('add_box_type.html')

@app.route('/deliveries_by_store_month', methods=['GET', 'POST'])
def deliveries_by_store_month():
    if request.method == 'POST':
        store_id = request.form.get('store_id', type=int)
        month = request.form.get('month', type=int)

        deliveries = Delivery.query.filter(
            Delivery.store_id == store_id,
            extract('month', Delivery.date) == month
        ).all()

        store = Store.query.get(store_id)
        return render_template('deliveries_by_store_month.html', deliveries=deliveries, store=store, month=month)

    stores = Store.query.all()
    return render_template('filter_store_month.html', stores=stores)

# --- Helper Function to Generate and Send Receipt ---
def generate_and_send_receipt(delivery):
    
    last_receipt_num = db.session.query(func.max(Delivery.receipt_number)).scalar()
    if last_receipt_num is None or last_receipt_num < 17421:
        new_receipt_num = 17421
    else:
        new_receipt_num = last_receipt_num + 1
    
    contact = delivery.store
    box_type = delivery.box_type # <-- Get BoxType from relationship
    
    # Use specific content if provided, otherwise use Box Type name
    if delivery.delivery_content:
        content_details = delivery.delivery_content
    else:
        content_details = box_type.type_name if box_type else "N/A"
    
    receipt_data = {
        'receipt_number': new_receipt_num,
        'delivery_date': delivery.date.strftime("%d/%m/%Y"),
        'store_name': contact.name,
        'store_address': contact.address or '',
        'delivery_content': content_details, # Pass the final content
        'quantity_carton': delivery.quantity_carton or '',
        'quantity_basket': delivery.quantity_basket or '',
        'vehicle_number': delivery.vehicle_number or '',
        'exit_time': delivery.exit_time or ''
    }
    
    html_out = render_template('receipt_template.html', **receipt_data)
    
    image_filename = f"receipt_{new_receipt_num}.png"
    image_filepath = os.path.join('static', image_filename) 
    abs_image_filepath = os.path.join(app.root_path, image_filepath)
    
    try:
        imgkit.from_string(html_out, abs_image_filepath, options={'format': 'png'})
    except IOError as e:
        print(f"Error generating image: {e}"); raise Exception(f"imgkit error: {e}")

    delivery.receipt_number = new_receipt_num
    delivery.generated_image_path = image_filepath 
    db.session.commit()
    
    customer_phone_api = normalize_phone_number(contact.phone)
    if not customer_phone_api:
        print(f"Skipping WhatsApp for {contact.name}: Invalid or missing phone number ({contact.phone}).")
        raise Exception(f"Invalid phone number for {contact.name}. Receipt generated but not sent.")

    try:
        caption = f"תעודת משלוח מס' {new_receipt_num}"
        command_to_run = [
            MUDSLIDE_PATH,
            'send-image',
            customer_phone_api,
            abs_image_filepath,
            '--caption',
            caption
        ]
        print(f"Running command: {' '.join(command_to_run)}")
        subprocess.run(
            command_to_run,
            check=True, 
            capture_output=True, 
            text=True
        ) 
        print(f"WhatsApp sent successfully to {customer_phone_api}")
    except subprocess.CalledProcessError as e:
        print(f"Error sending with Mudslide: {e.stderr}")
        raise Exception(f"Mudslide Error: {e.stderr}")
    except Exception as e:
        print(f"Unexpected error sending with Mudslide: {e}")
        raise e

# --- App Startup ---
if __name__ == '__main__':
    with app.app_context():
        static_dir = os.path.join(app.root_path, 'static')
        if not os.path.exists(static_dir): os.makedirs(static_dir)
        uploads_dir = os.path.join(app.root_path, 'static', 'uploads')
        if not os.path.exists(uploads_dir): os.makedirs(uploads_dir)
        
        if not os.path.exists(MUDSLIDE_PATH):
            print(f"CRITICAL ERROR: Mudslide executable not found at {MUDSLIDE_PATH}")
            print("Make sure you have run 'sudo chmod +x' on that file manually.")
        
        db.create_all()
    
    # Run on your specified host and port, WITHOUT ssl_context
    app.run(debug=True, host='192.168.1.3', port=5001)