# config.py
import os

class Config:
    # Your SECRET_KEY should be a long, random string.
    # For local development, it will use the default if not in .env.
    # For AWS deployment, it MUST be set as an environment variable on your EC2 instance.
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your_default_secret_key_for_local_testing'
    
    # AWS Configuration for DynamoDB and SNS
    # This region MUST match the region where your DynamoDB tables and SNS topic are created.
    # Your SNS ARN indicates 'us-east-1'.
    AWS_REGION = os.environ.get('AWS_REGION') or 'us-east-1a' 
    
    # DynamoDB Table Names
    # These MUST match the exact names of your tables in DynamoDB.
    DYNAMODB_USERS_TABLE = os.environ.get('DYNAMODB_USERS_TABLE') or 'travelgo_users'
    DYNAMODB_BOOKINGS_TABLE = os.environ.get('DYNAMODB_BOOKINGS_TABLE') or 'bookings' 
    
    # SNS Topic ARN
    # This MUST be the exact ARN of your SNS topic.
    # Your provided ARN: arn:aws:sns:us-east-1:664418997405:Travelgo:8b84921f-b2a4-41a4-965b-10a7622a81fd
    SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN') or 'arn:aws:sns:us-east-1:664418997405:Travelgo:8b84921f-b2a4-41a4-965b-10a7622a81fd'
