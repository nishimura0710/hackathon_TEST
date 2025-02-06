import redis
import os
import logging
from redis.retry import Retry
from redis.backoff import ExponentialBackoff

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RedisManager:
    def __init__(self):
        self.redis_client = None
        try:
            self.host = os.getenv('REDIS_HOST')
            if not self.host:
                logger.error("REDIS_HOST is missing")
                raise ValueError("REDIS_HOST is missing")
            
            self.port = int(os.getenv('REDIS_PORT', '6379'))
            self.password = os.getenv('REDIS_PASSWORD')
            if not self.password:
                logger.error("REDIS_PASSWORD is missing")
                raise ValueError("REDIS_PASSWORD is missing")
            
            logger.info(f"Initializing Redis connection to {self.host}:{self.port}")
            
            # Construct Redis URL from environment variables
            redis_url = f"rediss://:{self.password}@{self.host}:{self.port}"
            logger.info("Initializing Redis client with URL from environment")
            self.redis_client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=30,
                socket_connect_timeout=30,
                retry_on_timeout=True,
                ssl_cert_reqs=None
            )
            logger.info("Redis client created with SSL enabled")
            # Test connection
            self.redis_client.ping()
            logger.info("Redis connection test successful")
            logger.info("Redis client initialized")
            
        except Exception as e:
            logger.error(f"Redis initialization error: {str(e)}")
            raise

    def get_client(self):
        if not self.redis_client:
            logger.error("Redis client not initialized")
            raise ValueError("Redis client not initialized")
        return self.redis_client

redis_manager = RedisManager()

try:
    redis_client = redis_manager.get_client()
except Exception as e:
    logger.error(f"Failed to initialize Redis client: {e}")
    redis_client = None
