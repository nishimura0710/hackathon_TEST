import redis
import os
import json

redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'fly-calendar-bot-redis.upstash.io'),
    port=int(os.getenv('REDIS_PORT', '6379')),
    password=os.getenv('REDIS_PASSWORD', '676dd34052224c86a243e7e61401c5cc'),
    decode_responses=True
)

try:
    # Test Redis connection
    print('Testing Redis connection...')
    redis_client.ping()
    print('Redis connection successful')
    
    # Try to get stored credentials
    creds = redis_client.get('credentials:default_user')
    if creds:
        print('Found credentials:', json.loads(creds))
    else:
        print('No credentials found')
except Exception as e:
    print(f'Redis error: {str(e)}')
