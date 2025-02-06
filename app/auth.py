from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import json
from .redis_config import redis_client
import os
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events'
]

def create_flow():
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.getenv("OAUTH_REDIRECT_URI")],
        }
    }
    
    if not client_config["web"]["client_id"] or not client_config["web"]["client_secret"]:
        raise ValueError("OAuth認証情報が設定されていません")
        
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=os.getenv("OAUTH_REDIRECT_URI")
    )

@router.get("/google")
async def auth_google():
    try:
        flow = create_flow()
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        
        # Store state in Redis for validation
        redis_client.set(f"oauth_state:{state}", "pending", ex=3600)
        logger.info(f"Generated OAuth state: {state}")
        
        return {"auth_url": authorization_url}
    except FileNotFoundError as e:
        logger.error(f"OAuth initialization error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証の設定ファイルが見つかりません"
        )
    except Exception as e:
        logger.error(f"OAuth initialization error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証URLの生成に失敗しました"
        )

@router.get("/google/callback")
async def auth_callback(request: Request):
    try:
        # Log the incoming request URL and parameters
        logger.info(f"Received callback request: {request.url}")
        
        # Get state from query parameters
        state = request.query_params.get('state')
        if not state:
            logger.error("No state parameter in callback")
            raise HTTPException(
                status_code=400,
                detail="認証パラメータが不正です"
            )
            
        # Verify state exists in Redis
        stored_state = redis_client.get(f"oauth_state:{state}")
        if not stored_state:
            logger.error(f"Invalid state parameter: {state}")
            raise HTTPException(
                status_code=400,
                detail="認証セッションが無効です"
            )
            
        flow = create_flow()
        # Ensure HTTPS for OAuth callback
        callback_url = str(request.url)
        if callback_url.startswith('http://'):
            callback_url = 'https://' + callback_url[7:]
            
        logger.info(f"Fetching token with callback URL: {callback_url}")
        flow.fetch_token(
            authorization_response=callback_url
        )
        
        credentials = flow.credentials
        creds_dict = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        
        # Store credentials in Redis with 24-hour expiration
        redis_client.set(
            'credentials:default_user',
            json.dumps(creds_dict),
            ex=86400  # 24 hours
        )
        logger.info("Stored OAuth credentials in Redis with 24-hour expiration")
        
        # Store refresh token separately with longer expiration
        if credentials.refresh_token:
            redis_client.set(
                'refresh_token:default_user',
                credentials.refresh_token,
                ex=2592000  # 30 days
            )
            logger.info("Stored refresh token in Redis with 30-day expiration")
        
        # Redirect back to frontend
        frontend_url = os.getenv('FRONTEND_URL')
        if not frontend_url:
            raise ValueError("FRONTEND_URL environment variable is not set")
            
        return RedirectResponse(
            url=frontend_url,
            status_code=302
        )
    except ValueError as e:
        logger.error(f"OAuth callback configuration error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証の設定が正しくありません"
        )
    except Exception as e:
        logger.error(f"OAuth callback error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証処理に失敗しました"
        )

@router.get("/check")
async def check_auth():
    try:
        creds_json = redis_client.get('credentials:default_user')
        logger.info(f"Checking credentials: {'Found' if creds_json else 'Not found'}")
        
        if not creds_json:
            # Try to refresh using stored refresh token
            refresh_token = redis_client.get('refresh_token:default_user')
            logger.info(f"Checking refresh token: {'Found' if refresh_token else 'Not found'}")
            
            if refresh_token:
                try:
                    flow = create_flow()
                    credentials = Credentials(
                        None,
                        refresh_token=refresh_token,
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.getenv("GOOGLE_CLIENT_ID"),
                        client_secret=os.getenv("GOOGLE_CLIENT_SECRET")
                    )
                    
                    # Refresh the token
                    logger.info("Attempting to refresh token...")
                    credentials.refresh(flow.credentials)
                    logger.info("Token refresh successful")
                    
                    # Store new credentials
                    creds_dict = {
                        'token': credentials.token,
                        'refresh_token': refresh_token,
                        'token_uri': credentials.token_uri,
                        'client_id': credentials.client_id,
                        'client_secret': credentials.client_secret,
                        'scopes': credentials.scopes
                    }
                    
                    redis_client.set(
                        'credentials:default_user',
                        json.dumps(creds_dict),
                        ex=86400  # 24 hours
                    )
                    logger.info("Successfully refreshed and stored new credentials")
                    return {"authenticated": True}
                except Exception as refresh_error:
                    logger.error(f"Token refresh error: {str(refresh_error)}")
                    raise HTTPException(status_code=401, detail="認証の更新に失敗しました")
            
            logger.info("No valid credentials or refresh token found")
            raise HTTPException(status_code=401, detail="認証が必要です")
            
        logger.info("Valid credentials found")
        return {"authenticated": True}
    except Exception as e:
        logger.error(f"Auth check error: {str(e)}")
        raise HTTPException(status_code=401, detail="認証が必要です")
