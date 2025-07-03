# config.py
import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a_very_secret_key_that_should_be_in_env'
    
    # AWS Configuration for DynamoDB and SNS
    AWS_REGION = os.environ.get('AWS_REGION') or 'ap-south-1' # Example region, change as needed
    DYNAMODB_USERS_TABLE = os.environ.get('DYNAMODB_USERS_TABLE') or 'travelgo_users'
    DYNAMODB_BOOKINGS_TABLE = os.environ.get('DYNAMODB_BOOKINGS_TABLE') or 'bookings' 
    SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN') # This should be set in your AWS environment