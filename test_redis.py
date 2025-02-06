import redis
from redis.exceptions import ConnectionError, TimeoutError
import os
from dotenv import load_dotenv
import json
import socket

# Load environment variables from .env file
load_dotenv()

def test_redis_connection():
    # Get Redis configuration from environment
    host = os.getenv('REDIS_HOST')
    port = os.getenv('REDIS_PORT', '6379')
    password = os.getenv('REDIS_PASSWORD')
    
    if not all([host, password]):
        print("Missing required Redis environment variables")
        return
    
    redis_url = f"rediss://default:{password}@{host}:{port}"
    print("\nAttempting Redis connection...")
    print(f"Redis URL: {redis_url}")
    
    try:
        print("\nTesting Upstash Redis connection...")
        redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=10,
            socket_connect_timeout=10,
            retry_on_timeout=True
        )
        
        # Test connection
        redis_client.ping()
        print("Redis connection successful!")
        
        # Try to get stored credentials
        creds = redis_client.get('credentials:default_user')
        if creds:
            print('Found credentials:', json.loads(creds))
        else:
            print('No credentials found')
        
        # Try to get stored credentials
        creds = redis_client.get('credentials:default_user')
        if creds:
            print('Found credentials:', json.loads(creds))
        else:
            print('No credentials found')
            
    except redis.ConnectionError as e:
        print(f"\nRedis connection error: {str(e)}")
        print("\nDetailed error information:")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
    except socket.gaierror as e:
        print(f"\nDNS resolution error: {str(e)}")
    except Exception as e:
        print(f"\nUnexpected error: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
    finally:
        print("\nTest completed.")

if __name__ == "__main__":
    test_redis_connection()
