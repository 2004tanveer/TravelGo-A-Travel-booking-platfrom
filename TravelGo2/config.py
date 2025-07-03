# config.py
import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a_very_secret_key_that_should_be_in_env'
    
    # AWS Configuration for DynamoDB and SNS
    AWS_REGION = os.environ.get('us-east-1') # Example region, change as needed
    DYNAMODB_USERS_TABLE = os.environ.get('DYNAMODB_USERS_TABLE') or 'travelgo_users'
    DYNAMODB_BOOKINGS_TABLE = os.environ.get('DYNAMODB_BOOKINGS_TABLE') or 'bookings' 
    SNS_TOPIC_ARN = os.environ.get('ARN:arn:aws:sns:us-east-1:664418997405:Travelgo:8b84921f-b2a4-41a4-965b-10a7622a81fd') # This should be set in your AWS environment
