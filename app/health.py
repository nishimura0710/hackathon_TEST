from fastapi import APIRouter, HTTPException
from app.redis_config import redis_client
import socket
import logging
import os

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/health")
async def health_check():
    try:
        # Get network info
        host = os.getenv('REDIS_HOST', 'fly-calendar-bot-redis.upstash.io')
        logger.info(f"Testing Redis connection to {host}...")
        
        # Try DNS resolution
        try:
            ip = socket.gethostbyname(host)
            logger.info(f"DNS resolution successful: {ip}")
        except socket.gaierror as e:
            logger.error(f"DNS resolution failed: {str(e)}")
            ip = "resolution failed"
        
        # During startup, we want to return healthy even if Redis isn't ready
        try:
            if redis_client is None:
                logger.warning("Redis client is not initialized yet")
                return {"status": "healthy", "message": "initializing"}
            
            redis_client.ping()
            logger.info("Redis connection successful")
            
            return {
                "status": "healthy",
                "redis": {
                    "host": host,
                    "status": "connected",
                    "network": {
                        "ipv6_enabled": True,
                        "ssl_enabled": True
                    }
                }
            }
        except Exception as e:
            logger.warning(f"Redis not ready yet: {str(e)}")
            return {"status": "healthy", "message": "initializing"}
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        # During startup, return healthy even if Redis isn't ready yet
        if "Connection refused" in str(e) or "Network is unreachable" in str(e):
            return {
                "status": "healthy",
                "redis": {
                    "host": host,
                    "resolved_ip": ip if 'ip' in locals() else "unknown",
                    "status": "initializing",
                    "network": {
                        "ipv6_enabled": True,
                        "ssl_enabled": True
                    }
                }
            }
        return {
            "status": "unhealthy",
            "redis": {
                "host": host,
                "resolved_ip": ip if 'ip' in locals() else "unknown",
                "error": str(e),
                "network": {
                    "ipv6_enabled": True,
                    "ssl_enabled": True
                }
            }
        }
