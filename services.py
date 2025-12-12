import logging
import time
from datetime import datetime, timedelta
from extensions import db
from models import Token, SystemConfig, RequestLog
from zai_token import DiscordOAuthHandler
import jwt # pyjwt
from flask import current_app

logger = logging.getLogger(__name__)

def get_zai_handler():
    # Assume we are in app context so we can query SystemConfig
    config = SystemConfig.query.first()
    handler = DiscordOAuthHandler()
    if config and config.proxy_enabled and config.proxy_url:
        handler.session.proxies = {
            'http': config.proxy_url,
            'https': config.proxy_url
        }
    return handler

def update_token_info(token_id):
    # Caller must ensure app context
    token = Token.query.get(token_id)
    if not token:
        return False, "Token not found"

    handler = get_zai_handler()
    
    result = handler.backend_login(token.discord_token)
    
    if 'error' in result:
        token.error_count += 1
        token.remark = f"Refresh failed: {result['error']}"
        db.session.commit()
        return False, result['error']

    at = result.get('token')
    if at == 'SESSION_AUTH':
         user_info = result.get('user_info', {})
         token.email = user_info.get('email') or user_info.get('name')
         token.is_active = True
         token.error_count = 0
         token.zai_token = "SESSION_AUTH_COOKIE" 
         db.session.commit()
         return True, "Session Auth Active"
    
    token.zai_token = at
    token.error_count = 0
    
    # Decode JWT to get expiry and email
    try:
        decoded = jwt.decode(at, options={"verify_signature": False})
        if 'exp' in decoded:
            token.at_expires = datetime.fromtimestamp(decoded['exp'])
        if 'email' in decoded:
            token.email = decoded['email']
    except Exception as e:
        logger.warning(f"Failed to decode JWT: {e}")
        token.at_expires = datetime.now() + timedelta(seconds=3600)
        
    db.session.commit()
    return True, "Success"

def refresh_all_tokens():
    # Caller must ensure app context
    tokens = Token.query.filter_by(is_active=True).all()
    for token in tokens:
        if token.at_expires and token.at_expires > datetime.now() + timedelta(minutes=10):
            continue
        
        try:
            success, msg = update_token_info(token.id)
            logger.info(f"Refreshed token {token.id}: {msg}")
        except Exception as e:
            logger.error(f"Error refreshing token {token.id}: {e}")
