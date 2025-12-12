from extensions import db
from datetime import datetime

class SystemConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_username = db.Column(db.String(64), default='admin')
    admin_password_hash = db.Column(db.String(128))  # Store hash
    api_key = db.Column(db.String(128), default='sk-default-key')
    error_ban_threshold = db.Column(db.Integer, default=3)
    error_retry_count = db.Column(db.Integer, default=3) # New field
    debug_enabled = db.Column(db.Boolean, default=False)
    at_auto_refresh_enabled = db.Column(db.Boolean, default=True)
    token_refresh_interval = db.Column(db.Integer, default=3600)
    
    # Proxy Config
    proxy_enabled = db.Column(db.Boolean, default=False)
    proxy_url = db.Column(db.String(256), nullable=True)

    # Cache Config
    cache_enabled = db.Column(db.Boolean, default=False)
    cache_timeout = db.Column(db.Integer, default=7200)
    cache_base_url = db.Column(db.String(256), nullable=True)
    
    # Generation Timeout Config
    image_timeout = db.Column(db.Integer, default=300)
    video_timeout = db.Column(db.Integer, default=1500)

class Token(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=True) # Got from Zai
    discord_token = db.Column(db.String(512), nullable=False) # ST
    zai_token = db.Column(db.Text, nullable=True) # AT (JWT)
    at_expires = db.Column(db.DateTime, nullable=True)
    
    is_active = db.Column(db.Boolean, default=True)
    remark = db.Column(db.String(256), nullable=True)
    
    # Capabilities & Limits
    image_enabled = db.Column(db.Boolean, default=True)
    video_enabled = db.Column(db.Boolean, default=True)
    image_concurrency = db.Column(db.Integer, default=-1)
    video_concurrency = db.Column(db.Integer, default=-1)
    
    # Zai Account Info
    credits = db.Column(db.String(64), default='0')
    user_paygate_tier = db.Column(db.String(64), nullable=True)
    current_project_id = db.Column(db.String(64), nullable=True)
    current_project_name = db.Column(db.String(128), nullable=True)
    
    # Sora2 Info (from frontend JS logic)
    sora2_supported = db.Column(db.Boolean, default=False)
    sora2_total_count = db.Column(db.Integer, default=0)
    sora2_redeemed_count = db.Column(db.Integer, default=0)
    sora2_remaining_count = db.Column(db.Integer, default=0)
    sora2_invite_code = db.Column(db.String(64), nullable=True)
    
    # Stats
    error_count = db.Column(db.Integer, default=0)
    image_count = db.Column(db.Integer, default=0)
    video_count = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

class RequestLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    operation = db.Column(db.String(64)) # e.g. "chat/completions", "refresh"
    token_email = db.Column(db.String(120), nullable=True)
    status_code = db.Column(db.Integer)
    duration = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.now)
