import hashlib
import time
from typing import Optional, List, Dict
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.db.redis import get_redis
from app.models.account import Account
from app.services.auth_service import login_with_discord_token

logger = logging.getLogger(__name__)

def get_token_hash(discord_token: str) -> str:
    return hashlib.sha256(discord_token.encode()).hexdigest()

def get_zai_token_key(discord_token_hash: str) -> str:
    return f"zai:token:{discord_token_hash}"

def get_zai_limit_key(discord_token_hash: str) -> str:
    return f"zai:limit:{discord_token_hash}"

def get_zai_stats_key(discord_token_hash: str) -> str:
    return f"zai:stats:{discord_token_hash}"

async def increment_token_stats(discord_token_hash: str, success: bool = True):
    redis = await get_redis()
    key = get_zai_stats_key(discord_token_hash)
    field = "success" if success else "failure"
    await redis.hincrby(key, field, 1)

async def refresh_account_token(session: AsyncSession, account: Account) -> bool:
    """
    Refresh Zai token for a specific account.
    Returns True if successful, False otherwise.
    """
    logger.info(f"Refreshing token for account {account.id}")
    
    result = await login_with_discord_token(account.discord_token)
    
    if "error" in result:
        error_msg = result["error"]
        logger.error(f"Failed to refresh token for account {account.id}: {error_msg}")
        
        # Update last error in DB
        account.last_error = str(error_msg)
        
        # Should we disable the account? Maybe on specific errors like 401/Invalid Token
        if "无效" in str(error_msg) or "Auth" in str(error_msg):
            account.is_active = False
            logger.warning(f"Disabling account {account.id} due to auth failure")
            
        await session.commit()
        return False
    
    zai_token = result.get("token")
    if not zai_token:
        logger.error(f"No token returned for account {account.id}")
        return False
        
    # Store in Redis
    redis = await get_redis()
    token_hash = get_token_hash(account.discord_token)
    key = get_zai_token_key(token_hash)
    
    # Set with TTL
    await redis.set(key, zai_token, ex=settings.ZAI_TOKEN_TTL)
    
    # Store reverse mapping for invalidation: zai_token -> discord_token_hash
    # We use a hash of zai_token to keep key short
    zai_token_hash = hashlib.sha256(zai_token.encode()).hexdigest()
    reverse_key = f"zai:reverse:{zai_token_hash}"
    await redis.set(reverse_key, token_hash, ex=settings.ZAI_TOKEN_TTL)
    
    logger.info(f"Successfully refreshed token for account {account.id}")
    
    # Clear error if any
    if account.last_error:
        account.last_error = None
        await session.commit()
        
    return True

async def get_valid_zai_token(session: AsyncSession) -> Optional[tuple[str, str]]:
    """
    Get a valid, rate-limit-free Zai token.
    Returns (zai_token, discord_token_hash) or None.
    """
    redis = await get_redis()
    
    # 1. Get all active accounts from DB
    stmt = select(Account).where(Account.is_active == True)
    result = await session.execute(stmt)
    accounts = result.scalars().all()
    
    if not accounts:
        logger.warning("No active accounts found")
        return None
        
    # 2. Check Redis for availability and rate limits
    # This is a simple implementation. For high concurrency, we might need a better pool structure in Redis.
    
    for account in accounts:
        token_hash = get_token_hash(account.discord_token)
        token_key = get_zai_token_key(token_hash)
        limit_key = get_zai_limit_key(token_hash)
        
        # Check if rate limited
        is_limited = await redis.exists(limit_key)
        if is_limited:
            continue
            
        # Check if zai token exists
        zai_token = await redis.get(token_key)
        
        # If token missing but account active, trigger async refresh and skip for now
        # (The background worker should handle this usually)
        if not zai_token:
            # Optionally trigger background refresh here if we want reactive refresh
            continue
            
        # Found a valid candidate!
        # Apply rate limit lock immediately
        # Use setnx to avoid race condition if multiple workers try to pick same token?
        # Ideally: set(limit_key, 1, ex=60, nx=True)
        acquired = await redis.set(limit_key, "1", ex=60, nx=True)
        if acquired:
            return zai_token, token_hash
            
    return None

async def mark_token_invalid(zai_token: str):
    """
    Handle 401 Unauthorized from Zai API.
    Find which account owns this token and delete it to trigger refresh.
    """
    redis = await get_redis()
    zai_token_hash = hashlib.sha256(zai_token.encode()).hexdigest()
    reverse_key = f"zai:reverse:{zai_token_hash}"
    
    discord_token_hash = await redis.get(reverse_key)
    
    if discord_token_hash:
        logger.warning(f"Invalidating token for hash {discord_token_hash}")
        # Delete the main token key
        token_key = get_zai_token_key(discord_token_hash)
        await redis.delete(token_key)
        await redis.delete(reverse_key)
        
        # Increment failure stats
        await increment_token_stats(discord_token_hash, success=False)