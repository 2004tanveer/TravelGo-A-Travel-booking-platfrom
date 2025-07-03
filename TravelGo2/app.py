import os
import json
import uuid
import random
from datetime import datetime, timedelta
from decimal import Decimal # Use Decimal for numbers in DynamoDB

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

# --- IMPORTANT: This loads environment variables from your .env file ---
# For AWS deployment, you will set these environment variables directly on your EC2 instance
# (e.g., via user data, systemd service file, or AWS Systems Manager Parameter Store)
# The `load_dotenv()` call is primarily for local development convenience.
from dotenv import load_dotenv
load_dotenv() 
# --- END IMPORTANT ---

# Assume config.py exists and contains a Config class
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirect to login if user tries to access a protected page
login_manager.login_message_category = "info" # Category for flash message when login is required

# --- DEBUGGING CHANGE HERE ---
# Get region from app.config, but provide a hardcoded fallback if it's still somehow missing.
# This helps diagnose if app.config is not getting the value from Config.
aws_region_to_use = app.config.get('AWS_REGION')
if not aws_region_to_use:
    print("DEBUG: AWS_REGION was not found in app.config. Falling back to hardcoded 'us-east-1'.")
    aws_region_to_use = 'us-east-1' # Hardcoded fallback
else:
    print(f"DEBUG: AWS_REGION from app.config is: {aws_region_to_use}")
# --- END DEBUGGING CHANGE ---


# Initialize AWS DynamoDB and SNS clients
# Boto3 will automatically pick up credentials from IAM roles on EC2
dynamodb = boto3.resource('dynamodb', region_name=aws_region_to_use)
users_table = dynamodb.Table(app.config['DYNAMODB_USERS_TABLE'])
bookings_table = dynamodb.Table(app.config['DYNAMODB_BOOKINGS_TABLE'])

sns_client = boto3.client('sns', region_name=aws_region_to_use) # Use the determined region

class User(UserMixin):
    # For DynamoDB without GSI, 'email' will be the primary key for the User table
    # and thus the Flask-Login 'id'.
    def __init__(self, email, password_hash, username=None):
        self.email = email
        self.password = password_hash  # This is the HASHED password
        self.username = username # Optional, if you store username separately
        self.id = email # Flask-Login expects 'id' as a unique identifier

    @staticmethod
    def get(email):
        try:
            response = users_table.get_item(Key={'email': email})
            user_data = response.get('Item')
            if user_data:
                # Ensure username is retrieved if it exists
                username = user_data.get('username') 
                return User(user_data['email'], user_data['password'], username)
            return None
        except ClientError as e:
            app.logger.error(f"DynamoDB error getting user: {e.response['Error']['Message']}")
            return None

@login_manager.user_loader
def load_user(user_id):
    # In this setup, user_id is the email
    return User.get(user_id)

# --- SNS Notification Function ---
def send_sns_notification(subject, message, email_address=None):
    """Sends an SNS notification. If email_address is provided, it attempts to
    send directly to that email via SNS, assuming the topic is configured for it.
    Otherwise, it publishes to the general topic ARN."""
    
    sns_topic_arn = app.config.get('SNS_TOPIC_ARN')
    if not sns_topic_arn:
        app.logger.warning("SNS_TOPIC_ARN not configured. Skipping notification.")
        return

    try:
        # If an email address is provided and it's a valid email, publish directly to it
        # This assumes your SNS topic has an email subscription or is set up for direct publishing.
        if email_address and "@" in email_address:
             response = sns_client.publish(
                TopicArn=sns_topic_arn,
                Message=message,
                Subject=subject
            )
        else:
            # Fallback to publishing to the topic without a specific email (if you have other subscribers)
            response = sns_client.publish(
                TopicArn=sns_topic_arn,
                Message=message,
                Subject=subject
            )
        app.logger.info(f"SNS notification sent: {response['MessageId']}")
    except ClientError as e:
        app.logger.error(f"Failed to send SNS notification: {e.response['Error']['Message']}")
    except Exception as e:
        app.logger.error(f"An unexpected error occurred while sending SNS notification: {e}")


# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email') # Login strictly by email (Partition Key)
        password = request.form.get('password')

        user_data = None
        try:
            response = users_table.get_item(Key={'email': email})
            user_data = response.get('Item')
        except ClientError as e:
            app.logger.error(f"DynamoDB error during login lookup: {e.response['Error']['Message']}")
            flash('An error occurred during login. Please try again.', 'danger')
            return render_template('auth/login.html')

        if user_data and check_password_hash(user_data['password'], password):
            user_obj = User(user_data['email'], user_data['password'], user_data.get('username'))
            login_user(user_obj)
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next') # Redirect to the page user was trying to access
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid email or password.', 'danger')
    return render_template('auth/login.html') # Assuming login.html is in auth/

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username') # Added username field
        email = request.form.get('email')
        password = request.form.get('password')

        # Basic validation
        if not username or not email or not password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('register')) # Assuming register.html in auth/
        
        try:
            # Check if email already exists (primary key lookup)
            response = users_table.get_item(Key={'email': email})
            if 'Item' in response:
                flash('Email already registered. Please use another or log in.', 'danger')
                return redirect(url_for('register'))
            
            # Note: Without GSI, we cannot efficiently check for unique usernames.
            # If username uniqueness is critical, a GSI would be required.

            hashed_password = generate_password_hash(password)
            
            users_table.put_item(
                Item={
                    'email': email, # Primary Partition Key
                    'username': username,
                    'password': hashed_password,
                    'created_at': datetime.now().isoformat() # Store datetime as ISO format string
                },
                ConditionExpression='attribute_not_exists(email)' # Ensures email is unique
            )
            flash('Registration successful! You can now log in.', 'success')
            return redirect(url_for('login'))
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                flash('User with this email already exists.', 'danger')
            else:
                app.logger.error(f"DynamoDB error during registration: {e.response['Error']['Message']}")
                flash('An error occurred during registration. Please try again.', 'danger')
            return redirect(url_for('register'))
        except Exception as e:
            app.logger.error(f"An unexpected error occurred during registration: {e}")
            flash('An unexpected error occurred. Please try again.', 'danger')
            return redirect(url_for('register'))
    return render_template('auth/register.html') # Assuming register.html in auth/

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/my_bookings')
@login_required
def my_bookings():
    user_bookings = []
    try:
        # Query bookings for the current logged-in user using the composite primary key
        # 'user_email' (Partition Key) and 'booking_date' (Sort Key)
        response = bookings_table.query(
            KeyConditionExpression=Key('user_email').eq(current_user.email),
            ScanIndexForward=False # Get most recent bookings first
        )
        user_bookings = response.get('Items', [])
        
        # Deserialize the 'details' field from JSON string to dictionary
        # Convert booking_date back to datetime object for strftime
        for booking in user_bookings:
            if 'details' in booking and isinstance(booking['details'], str):
                try:
                    booking['details'] = json.loads(booking['details'], parse_float=Decimal) # Parse numbers as Decimal
                except json.JSONDecodeError:
                    app.logger.error(f"Failed to decode JSON for booking details: {booking.get('details')}")
                    booking['details'] = {} # Default to empty dict on error
            
            # Map booking_id to _id for template compatibility if needed
            booking['_id'] = booking.get('booking_id', str(uuid.uuid4())) # Use booking_id or generate fallback

            if 'booking_date' in booking and isinstance(booking['booking_date'], str):
                try:
                    booking['booking_date'] = datetime.fromisoformat(booking['booking_date'])
                except ValueError:
                    app.logger.warning(f"Could not parse booking_date: {booking['booking_date']}")
                    pass # Handle cases where date format might be off or missing
            
            # Convert Decimal values in details to float for easier template display if necessary
            if 'details' in booking and isinstance(booking['details'], dict):
                for key, value in booking['details'].items():
                    if isinstance(value, Decimal):
                        booking['details'][key] = float(value)
            if 'total_price' in booking and isinstance(booking['total_price'], Decimal):
                booking['total_price'] = float(booking['total_price'])
            if 'price_per_person' in booking and isinstance(booking['price_per_person'], Decimal):
                booking['price_per_person'] = float(booking['price_per_person'])
            if 'price_per_night' in booking and isinstance(booking['price_per_night'], Decimal):
                booking['price_per_night'] = float(booking['price_per_night'])


    except ClientError as e:
        app.logger.error(f"DynamoDB error fetching bookings: {e.response['Error']['Message']}")
        flash('Could not retrieve your bookings. Please try again.', 'danger')
    except Exception as e:
        app.logger.error(f"An unexpected error occurred fetching bookings: {e}")
        flash('An unexpected error occurred. Please try again.', 'danger')
        
    return render_template('my_bookings.html', bookings=user_bookings)


# --- DEDICATED SEARCH PAGES ROUTES ---
# These routes still use dummy data as before.
# In a real app, you'd integrate with external APIs or a more complex data source.

@app.route('/flights')
def flights_page():
    return render_template('flight.html')

@app.route('/hotels')
def hotels_page():
    return render_template('hotel.html')

@app.route('/trains')
def trains_page():
    return render_template('trains.html')

@app.route('/buses')
def buses_page():
    return render_template('bus.html')


# --- Search Results Routes (using dummy data for now) ---
@app.route('/search/flights', methods=['POST'])
def search_flights():
    from_city = request.form.get('flightFrom')
    to_city = request.form.get('flightTo')
    departure_date = request.form.get('flightDeparture')
    return_date = request.form.get('flightReturn')
    passengers = request.form.get('flightPassengers')

    dummy_flights = []
    for i in range(1, 6): # Generate 5 dummy flights
        flight_id = str(uuid.uuid4()) # Unique ID for each dummy flight
        dummy_flights.append({
            'id': flight_id,
            'airline': f'Airline {i}',
            'from': from_city,
            'to': to_city,
            'departure_date': departure_date,
            'return_date': return_date if return_date else 'N/A',
            'departure_time': f'{8 + i}:00 AM',
            'arrival_time': f'{10 + i}:00 AM',
            'duration': f'{2 + (i*0.5)}h',
            'price': Decimal(str(100 + (i * 25))), # Ensure Decimal for prices
            'stops': 0 if i % 2 == 0 else 1
        })
    flash(f"Searching flights from {from_city} to {to_city} on {departure_date}...", "info")
    return render_template('search_results/flights_results.html', flights=dummy_flights, search_params=request.form)

@app.route('/search/hotels', methods=['POST'])
def search_hotels():
    destination = request.form.get('hotelDestination')
    checkin_date = request.form.get('hotelCheckin')
    checkout_date = request.form.get('hotelCheckout')
    guests = request.form.get('hotelGuests')

    dummy_hotels = []
    for i in range(1, 6):
        hotel_id = str(uuid.uuid4())
        dummy_hotels.append({
            'id': hotel_id,
            'name': f'Hotel {i} {destination}',
            'location': destination,
            'checkin': checkin_date,
            'checkout': checkout_date,
            'guests': guests,
            'price_per_night': Decimal(str(80 + (i * 15))), # Ensure Decimal
            'rating': Decimal(str(round(3.5 + (i * 0.2), 1))), # Ensure Decimal
            'image': f'/static/images/hotel{i}.jpg'
        })
    flash(f"Searching hotels in {destination} from {checkin_date} to {checkout_date}...", "info")
    return render_template('search_results/hotels_results.html', hotels=dummy_hotels, search_params=request.form)

@app.route('/search/trains', methods=['POST'])
def search_trains():
    from_station = request.form.get('trainFrom')
    to_station = request.form.get('trainTo')
    travel_date = request.form.get('trainDate')
    train_class = request.form.get('trainClass')
    passengers = request.form.get('trainPassengers')

    dummy_trains = []
    for i in range(1, 6):
        train_id = str(uuid.uuid4())
        dummy_trains.append({
            'id': train_id,
            'train_number': f'TRN{100 + i}',
            'from': from_station,
            'to': to_station,
            'departure_date': travel_date,
            'departure_time': f'{6 + i}:30 AM',
            'arrival_time': f'{12 + i}:00 PM',
            'travel_class': train_class,
            'price': Decimal(str(50 + (i * 10))) # Ensure Decimal
        })
    flash(f"Searching trains from {from_station} to {to_station} on {travel_date}...", "info")
    return render_template('search_results/trains_results.html', trains=dummy_trains, search_params=request.form)


@app.route('/search/buses', methods=['POST'])
def search_buses():
    from_city = request.form.get('busFrom')
    to_city = request.form.get('busTo')
    travel_date = request.form.get('busDate')
    passengers = request.form.get('busPassengers')

    dummy_buses = []
    for i in range(1, 6):
        bus_id = str(uuid.uuid4())
        dummy_buses.append({
            'id': bus_id,
            'operator': f'BusCo {i}',
            'from': from_city,
            'to': to_city,
            'departure_date': travel_date,
            'departure_time': f'{7 + i}:15 AM',
            'arrival_time': f'{11 + i}:45 AM',
            'price': Decimal(str(30 + (i * 5))) # Ensure Decimal
        })
    flash(f"Searching buses from {from_city} to {to_city} on {travel_date}...", "info")
    return render_template('search_results/buses_results.html', buses=dummy_buses, search_params=request.form)

# --- Selection Routes ---
# These routes prepare the data and store it in session for the final confirmation.

@app.route('/select_flight_seats/<string:flight_id>', methods=['GET'])
@login_required
def select_flight_seats(flight_id):
    search_params = request.args 
    selected_flight = {
        'id': flight_id, 
        'airline': search_params.get('airline', 'Unknown Airline'),
        'from': search_params.get('from'),
        'to': search_params.get('to'),
        'departure_date': search_params.get('departure_date'),
        'return_date': search_params.get('return_date') if search_params.get('return_date') else 'N/A',
        'departure_time': search_params.get('departure_time', 'N/A'),
        'arrival_time': search_params.get('arrival_time', 'N/A'),
        'duration': search_params.get('duration', 'N/A'),
        'price': Decimal(search_params.get('price', '0')), # Convert to Decimal
        'stops': int(search_params.get('stops', 0)),
        'flightPassengers': int(search_params.get('flightPassengers', '1 Adult').split(' ')[0]) # Extract number from "X Adults"
    }
    session['pending_booking'] = selected_flight # Store for final confirmation
    return render_template('selection/flight_selection.html', flight=selected_flight, search_params=search_params)

@app.route('/select_hotel_room/<string:hotel_id>', methods=['GET'])
@login_required
def select_hotel_room(hotel_id):
    search_params = request.args
    selected_hotel = {
        'id': hotel_id,
        'name': search_params.get('name', 'Unknown Hotel'),
        'location': search_params.get('location'),
        'checkin': search_params.get('checkin'),
        'checkout': search_params.get('checkout'),
        'guests': search_params.get('guests'),
        'price_per_night': Decimal(search_params.get('price_per_night', '0')), # Convert to Decimal
        'rating': Decimal(search_params.get('rating', '0.0')),
        'image': search_params.get('image', '')
    }
    session['pending_booking'] = selected_hotel # Store for final confirmation
    return render_template('selection/hotel_selection.html', hotel=selected_hotel, search_params=search_params)

@app.route('/select_train_seats/<string:train_id>', methods=['GET'])
@login_required
def select_train_seats(train_id):
    search_params = request.args
    selected_train = {
        'id': train_id,
        'train_number': search_params.get('train_number', 'N/A'),
        'from': search_params.get('from'),
        'to': search_params.get('to'),
        'departure_date': search_params.get('departure_date'),
        'departure_time': search_params.get('departure_time', 'N/A'),
        'arrival_time': search_params.get('arrival_time', 'N/A'),
        'travel_class': search_params.get('travel_class', 'N/A'),
        'price': Decimal(search_params.get('price', '0')), # Convert to Decimal
        'trainPassengers': int(search_params.get('trainPassengers', '1 Adult').split(' ')[0]) # Extract number
    }

    # Fetch already booked seats for this train and date using SCAN (no GSI)
    # WARNING: This uses a scan operation, which can be inefficient and costly on large tables.
    # For better performance, consider adding a GSI on 'details.id' and 'details.departure_date'
    # in your 'bookings' table for 'train' booking_type.
    booked_seats = set()
    try:
        response = bookings_table.scan(
            FilterExpression=Attr('details.id').eq(train_id) &
                             Attr('details.departure_date').eq(selected_train['departure_date']) &
                             Attr('booking_type').eq('train') &
                             Attr('details.selected_train_seat').exists() # Only bookings with seats
        )
        for b in response.get('Items', []):
            # Deserialize details to access selected_train_seat
            if 'details' in b and isinstance(b['details'], str):
                details_dict = json.loads(b['details'])
                if 'selected_train_seat' in details_dict:
                    booked_seats.add(details_dict['selected_train_seat'])
    except ClientError as e:
        app.logger.error(f"DynamoDB error fetching booked train seats (SCAN): {e.response['Error']['Message']}")
        flash('Could not retrieve seat availability. Please try again.', 'danger')

    all_seats = [f"S{i}" for i in range(1, 101)] # Assuming 100 seats per train
    
    session['pending_booking'] = selected_train # Store for final confirmation
    return render_template('selection/train_selection.html', train=selected_train, search_params=search_params, booked_seats=booked_seats, all_seats=all_seats)

@app.route('/select_bus_seats/<string:bus_id>', methods=['GET'])
@login_required
def select_bus_seats(bus_id):
    search_params = request.args
    selected_bus = {
        'id': bus_id,
        'operator': search_params.get('operator', 'N/A'),
        'from': search_params.get('from'),
        'to': search_params.get('to'),
        'departure_date': search_params.get('departure_date'),
        'departure_time': search_params.get('departure_time', 'N/A'),
        'arrival_time': search_params.get('arrival_time', 'N/A'),
        'price': Decimal(search_params.get('price', '0')), # Convert to Decimal
        'busPassengers': int(search_params.get('busPassengers', '1 Adult').split(' ')[0]) # Extract number
    }

    # Fetch already booked seats for this bus and date using SCAN (no GSI)
    # WARNING: This uses a scan operation, which can be inefficient and costly on large tables.
    # For better performance, consider adding a GSI on 'details.id' and 'details.departure_date'
    # in your 'bookings' table for 'bus' booking_type.
    booked_seats = set()
    try:
        response = bookings_table.scan(
            FilterExpression=Attr('details.id').eq(bus_id) &
                             Attr('details.departure_date').eq(selected_bus['departure_date']) &
                             Attr('booking_type').eq('bus') &
                             Attr('details.selected_bus_seat').exists() # Only bookings with seats
        )
        for b in response.get('Items', []):
            # Deserialize details to access selected_bus_seat
            if 'details' in b and isinstance(b['details'], str):
                details_dict = json.loads(b['details'])
                if 'selected_bus_seat' in details_dict:
                    booked_seats.add(details_dict['selected_bus_seat'])
    except ClientError as e:
        app.logger.error(f"DynamoDB error fetching booked bus seats (SCAN): {e.response['Error']['Message']}")
        flash('Could not retrieve seat availability. Please try again.', 'danger')

    all_seats = [f"S{i}" for i in range(1, 41)] # Assuming 40 seats per bus
    
    session['pending_booking'] = selected_bus # Store for final confirmation
    return render_template('selection/bus_selection.html', bus=selected_bus, search_params=search_params, booked_seats=booked_seats, all_seats=all_seats)


# --- Generic Booking Confirmation Route ---
@app.route('/confirm_booking/<string:booking_type>/<string:item_id>', methods=['POST'])
@login_required
def confirm_booking(booking_type, item_id):
    # Retrieve pending booking details from session
    booking_data = session.pop('pending_booking', None)
    if not booking_data or booking_data.get('id') != item_id:
        flash("Booking failed! Session expired or invalid item.", "danger")
        return redirect(url_for('index')) # Redirect to home or relevant search page

    # Get additional details from the form submission (e.g., selected seat/room)
    form_data = request.form.to_dict()
    form_data.pop('csrf_token', None) # Remove Flask-specific internal fields

    # Merge form data into booking_data's details
    final_booking_details = booking_data.copy()
    final_booking_details.update(form_data) # Overwrite/add form fields

    # Specific validation for seat/room selection
    if booking_type == 'flight':
        selected_seat = final_booking_details.get('selected_seat')
        if not selected_seat:
            flash("Please select a seat for your flight.", "danger")
            return redirect(url_for('select_flight_seats', flight_id=item_id, **booking_data))
    elif booking_type == 'hotel':
        selected_room_type = final_booking_details.get('selected_room_type')
        if not selected_room_type:
            flash("Please select a room type for your hotel.", "danger")
            return redirect(url_for('select_hotel_room', hotel_id=item_id, **booking_data))
    elif booking_type == 'train':
        selected_train_seat = final_booking_details.get('selected_train_seat')
        if not selected_train_seat:
            flash("Please select a seat for your train.", "danger")
            return redirect(url_for('select_train_seats', train_id=item_id, **booking_data))
        
        # Re-check train seat availability to prevent double booking using SCAN (no GSI)
        booked_seats_current = set()
        try:
            response = bookings_table.scan(
                FilterExpression=Attr('details.id').eq(item_id) &
                                 Attr('details.departure_date').eq(final_booking_details['departure_date']) &
                                 Attr('booking_type').eq('train') &
                                 Attr('details.selected_train_seat').exists()
            )
            for b in response.get('Items', []):
                if 'details' in b and isinstance(b['details'], str):
                    details_dict = json.loads(b['details'])
                    if 'selected_train_seat' in details_dict:
                        booked_seats_current.add(details_dict['selected_train_seat'])
        except ClientError as e:
            app.logger.error(f"DynamoDB error re-checking train seats (SCAN): {e.response['Error']['Message']}")
            flash('An error occurred checking seat availability. Please try again.', 'danger')
            return redirect(url_for('select_train_seats', train_id=item_id, **booking_data))

        if selected_train_seat in booked_seats_current:
            flash("The selected train seat is no longer available. Please choose another.", "danger")
            return redirect(url_for('select_train_seats', train_id=item_id, **booking_data))

    elif booking_type == 'bus':
        selected_bus_seat = final_booking_details.get('selected_bus_seat')
        if not selected_bus_seat:
            flash("Please select a seat for your bus.", "danger")
            return redirect(url_for('select_bus_seats', bus_id=item_id, **booking_data))

        # Re-check bus seat availability to prevent double booking using SCAN (no GSI)
        booked_seats_current = set()
        try:
            response = bookings_table.scan(
                FilterExpression=Attr('details.id').eq(item_id) &
                                 Attr('details.departure_date').eq(final_booking_details['departure_date']) &
                                 Attr('booking_type').eq('bus') &
                                 Attr('details.selected_bus_seat').exists()
            )
            for b in response.get('Items', []):
                if 'details' in b and isinstance(b['details'], str):
                    details_dict = json.loads(b['details'])
                    if 'selected_bus_seat' in details_dict:
                        booked_seats_current.add(details_dict['selected_bus_seat'])
        except ClientError as e:
            app.logger.error(f"DynamoDB error re-checking bus seats (SCAN): {e.response['Error']['Message']}")
            flash('An error occurred checking seat availability. Please try again.', 'danger')
            return redirect(url_for('select_bus_seats', bus_id=item_id, **booking_data))

        if selected_bus_seat in booked_seats_current:
            flash("The selected bus seat is no longer available. Please choose another.", "danger")
            return redirect(url_for('select_bus_seats', bus_id=item_id, **booking_data))

    # Calculate total price for hotels based on nights
    if booking_type == 'hotel':
        try:
            checkin_date = datetime.fromisoformat(final_booking_details['checkin'])
            checkout_date = datetime.fromisoformat(final_booking_details['checkout'])
            nights = (checkout_date - checkin_date).days
            if nights <= 0:
                flash("Invalid check-in/check-out dates.", "danger")
                return redirect(url_for('select_hotel_room', hotel_id=item_id, **booking_data))
            final_booking_details['nights'] = nights
            final_booking_details['total_price'] = final_booking_details['price_per_night'] * Decimal(str(final_booking_details.get('num_rooms', 1))) * nights
        except ValueError:
            flash("Invalid date format for hotel booking.", "danger")
            return redirect(url_for('select_hotel_room', hotel_id=item_id, **booking_data))
    else: # For flights, trains, buses, total price is simpler
        # Ensure passenger count is correctly extracted and converted to Decimal
        num_passengers_str = final_booking_details.get(f'{booking_type}Passengers', '1 Adult')
        try:
            num_passengers = int(num_passengers_str.split(' ')[0])
        except (ValueError, IndexError):
            num_passengers = 1 # Default to 1 if parsing fails

        price_per_person = final_booking_details.get('price', Decimal('0'))
        final_booking_details['total_price'] = price_per_person * Decimal(str(num_passengers))


    # Prepare item for DynamoDB
    booking_id = str(uuid.uuid4())
    current_time_iso = datetime.now().isoformat()

    item_to_put = {
        'user_email': current_user.email, # Partition Key
        'booking_date': current_time_iso, # Sort Key (unique enough for new bookings)
        'booking_id': booking_id, # UUID as an attribute
        'booking_type': booking_type,
        'status': 'confirmed',
        'details': json.dumps(final_booking_details, default=str) # Convert Decimal to string for JSON
    }

    try:
        bookings_table.put_item(Item=item_to_put)
        flash_message = f'Your {booking_type} booking (ID: {booking_id[:8]}...) has been confirmed!'
        flash(flash_message, 'success')
        
        # Send SNS notification
        subject = f"TravelGo Booking Confirmation: {booking_type.capitalize()} (ID: {booking_id[:8]})"
        message = f"Hello {current_user.username or current_user.email},\n\nYour {booking_type} booking has been successfully confirmed!\n\nDetails:\n{json.dumps(final_booking_details, indent=2, default=str)}\n\nTotal Price: ${float(final_booking_details.get('total_price', 0))}\n\nThank you for choosing TravelGo!"
        send_sns_notification(subject, message, current_user.email)

    except ClientError as e:
        app.logger.error(f"DynamoDB error confirming booking: {e.response['Error']['Message']}")
        flash('Failed to confirm booking. Please try again.', 'danger')
    except Exception as e:
        app.logger.error(f"An unexpected error occurred during booking confirmation: {e}")
        flash('An unexpected error occurred. Please try again.', 'danger')

    return redirect(url_for('my_bookings'))

# --- Edit Booking Route ---
@app.route('/edit_booking/<string:booking_id>/<string:booking_date_iso>/<string:booking_type>', methods=['GET', 'POST'])
@login_required
def edit_booking(booking_id, booking_date_iso, booking_type):
    booking_item = None
    try:
        # Fetch item using composite primary key
        response = bookings_table.get_item(Key={'user_email': current_user.email, 'booking_date': booking_date_iso})
        booking_item = response.get('Item')
        
        # Deserialize details if found
        if booking_item and 'details' in booking_item and isinstance(booking_item['details'], str):
            booking_item['details'] = json.loads(booking_item['details'], parse_float=Decimal)
        
        # Convert Decimal values in details to float for easier template display if necessary
        if 'details' in booking_item and isinstance(booking_item['details'], dict):
            for key, value in booking_item['details'].items():
                if isinstance(value, Decimal):
                    booking_item['details'][key] = float(value)
        if 'total_price' in booking_item and isinstance(booking_item['total_price'], Decimal):
            booking_item['total_price'] = float(booking_item['total_price'])
        if 'price_per_person' in booking_item and isinstance(booking_item['price_per_person'], Decimal):
            booking_item['price_per_person'] = float(booking_item['price_per_person'])
        if 'price_per_night' in booking_item and isinstance(booking_item['price_per_night'], Decimal):
            booking_item['price_per_night'] = float(booking_item['price_per_night'])


    except ClientError as e:
        app.logger.error(f"DynamoDB error fetching booking for edit: {e.response['Error']['Message']}")
        flash('Could not retrieve booking for editing. Please try again.', 'danger')
        return redirect(url_for('my_bookings'))
    except Exception as e:
        app.logger.error(f"An unexpected error occurred fetching booking for edit: {e}")
        flash('An unexpected error occurred. Please try again.', 'danger')
        return redirect(url_for('my_bookings'))

    # Ensure the booking matches the booking_id from URL and belongs to current user
    if not booking_item or booking_item.get('booking_id') != booking_id or booking_item['user_email'] != current_user.email:
        flash('Booking not found or you do not have permission to edit it.', 'danger')
        return redirect(url_for('my_bookings'))

    if request.method == 'POST':
        updated_details_raw = request.form.to_dict()
        updated_details_raw.pop('csrf_token', None) # Clean up Flask-specific fields

        # Convert numbers in updated_details_raw to Decimal for DynamoDB compatibility
        for key, value in updated_details_raw.items():
            try:
                # Heuristic for numbers: try converting if key name suggests it's a price/rating/number
                if any(k_part in key.lower() for k_part in ['price', 'rating', 'total', 'num_']):
                    updated_details_raw[key] = Decimal(value)
            except Exception:
                pass # Keep as string if not a valid number

        # Reserialize updated details to JSON string for DynamoDB
        updated_details_json_str = json.dumps(updated_details_raw, default=str) # Convert Decimal to string

        try:
            bookings_table.update_item(
                Key={'user_email': current_user.email, 'booking_date': booking_date_iso},
                UpdateExpression="SET #details = :details_val, #last_updated = :last_updated_val",
                ExpressionAttributeNames={
                    '#details': 'details',
                    '#last_updated': 'last_updated'
                },
                ExpressionAttributeValues={
                    ':details_val': updated_details_json_str,
                    ':last_updated_val': datetime.now().isoformat()
                },
                ConditionExpression=boto3.dynamodb.conditions.Attr('user_email').eq(current_user.email) # Ensure user owns booking
            )
            flash('Booking updated successfully!', 'success')
            
            # Send SNS notification for update
            subject = f"TravelGo Booking Update: {booking_type.capitalize()} (ID: {booking_id[:8]})"
            message = f"Hello {current_user.username or current_user.email},\n\nYour {booking_type} booking has been successfully updated.\n\nNew Details:\n{json.dumps(updated_details_raw, indent=2, default=str)}\n\nThank you for choosing TravelGo!"
            send_sns_notification(subject, message, current_user.email)

        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                flash('You do not have permission to update this booking.', 'danger')
            else:
                app.logger.error(f"DynamoDB error updating booking: {e.response['Error']['Message']}")
                flash('Failed to update booking. Please try again.', 'danger')
        except Exception as e:
            app.logger.error(f"An unexpected error occurred during booking update: {e}")
            flash('An unexpected error occurred. Please try again.', 'danger')

        return redirect(url_for('my_bookings'))
        
    # For GET request, render the appropriate edit form
    # Pass the deserialized booking_item to the template
    # You will need to create these templates (e.g., templates/edit/flight_edit.html)
    if booking_type == 'flight':
        return render_template('edit/flight_edit.html', booking=booking_item)
    elif booking_type == 'hotel':
        return render_template('edit/hotel_edit.html', booking=booking_item)
    elif booking_type == 'train':
        return render_template('edit/train_edit.html', booking=booking_item)
    elif booking_type == 'bus':
        return render_template('edit/bus_edit.html', booking=booking_item)
    else:
        flash('Unknown booking type for editing.', 'danger')
        return redirect(url_for('my_bookings'))


# --- Cancel Booking Route ---
@app.route('/cancel_booking/<string:booking_id>/<string:booking_date_iso>', methods=['POST'])
@login_required
def cancel_booking(booking_id, booking_date_iso):
    try:
        # Fetch item using composite primary key to get full details for notification
        response = bookings_table.get_item(Key={'user_email': current_user.email, 'booking_date': booking_date_iso})
        booking = response.get('Item')

        if not booking or booking.get('booking_id') != booking_id or booking['user_email'] != current_user.email:
            flash('Booking not found or you do not have permission to cancel it.', 'danger')
            return redirect(url_for('my_bookings'))

        if booking['status'] == 'cancelled':
            flash('This booking is already cancelled.', 'info')
            return redirect(url_for('my_bookings'))

        # Update the booking status to 'cancelled'
        bookings_table.update_item(
            Key={'user_email': current_user.email, 'booking_date': booking_date_iso},
            UpdateExpression="SET #status = :status_val, #cancellation_date = :cancellation_date_val",
            ExpressionAttributeNames={
                '#status': 'status',
                '#cancellation_date': 'cancellation_date'
            },
            ExpressionAttributeValues={
                ':status_val': 'cancelled',
                ':cancellation_date_val': datetime.now().isoformat()
            },
            ConditionExpression=boto3.dynamodb.conditions.Attr('user_email').eq(current_user.email) # Ensure user owns booking
        )
        flash_message = f'Booking (ID: {booking_id[:8]}...) has been successfully cancelled.'
        flash(flash_message, 'success')

        # Send SNS notification for cancellation
        subject = f"TravelGo Booking Cancellation: {booking.get('booking_type', 'Booking')} (ID: {booking_id[:8]})"
        message = f"Hello {current_user.username or current_user.email},\n\nYour booking (ID: {booking_id[:8]}...) has been successfully cancelled.\n\nIf you have any questions, please contact support."
        send_sns_notification(subject, message, current_user.email) # Fixed: call the correct function name

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            flash('You do not have permission to cancel this booking.', 'danger')
        else:
            app.logger.error(f"DynamoDB error cancelling booking: {e.response['Error']['Message']}")
            flash('Failed to cancel booking. Please try again.', 'danger')
    except Exception as e:
        app.logger.error(f"An unexpected error occurred during booking cancellation: {e}")
        flash('An unexpected error occurred. Please try again.', 'danger')

    return redirect(url_for('my_bookings'))


if __name__ == '__main__':
    # This block is for local development only.
    # In production, you would use a WSGI server (like Gunicorn) to run the app.
    # Ensure your environment variables (SECRET_KEY, AWS_REGION, DYNAMODB_TABLES, SNS_TOPIC_ARN)
    # are set in your production environment (e.g., EC2 user data, systemd service, Parameter Store).
    app.run(debug=True, host='0.0.0.0')
