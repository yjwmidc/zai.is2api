import os
import time
import logging
import json
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, stream_with_context
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import requests

from extensions import db
from models import SystemConfig, Token, RequestLog
import services

# Initialize App
app = Flask(__name__, static_folder='static', template_folder='static')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///zai2api.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-change-me' # Should be random

# Initialize DB
db.init_app(app)

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    # We only have one admin user
    config = SystemConfig.query.first()
    if config and str(config.id) == user_id:
        return User(id=str(config.id), username=config.admin_username)
    return None

# Database Initialization
def init_db():
    with app.app_context():
        db.create_all()
        if not SystemConfig.query.first():
            # Default Admin: admin / admin
            # Use pbkdf2:sha256 which is default in generate_password_hash
            default_config = SystemConfig(
                admin_username='admin',
                admin_password_hash=generate_password_hash('admin')
            )
            db.session.add(default_config)
            db.session.commit()
            print("Initialized default admin/admin")

# Scheduler
def scheduled_refresh():
    with app.app_context():
        services.refresh_all_tokens()

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_refresh, 'interval', seconds=3600, id='token_refresher')
scheduler.start()

# --- Routes: Pages ---

@app.route('/login')
def login_page():
    return send_from_directory('static', 'login.html')

@app.route('/manage')
@login_required
def manage_page():
    return send_from_directory('static', 'manage.html')

@app.route('/')
def index():
    return send_from_directory('static', 'login.html')

# --- Routes: Auth API ---

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    config = SystemConfig.query.first()
    if config and config.admin_username == username and check_password_hash(config.admin_password_hash, password):
        user = User(id=str(config.id), username=config.admin_username)
        login_user(user)
        import jwt
        token = jwt.encode({'user_id': str(config.id), 'exp': datetime.utcnow().timestamp() + 86400}, app.config['SECRET_KEY'], algorithm='HS256')
        return jsonify({'success': True, 'token': token})
    
    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

# Middleware for Bearer Token Auth
def check_auth_token():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        import jwt
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            return payload.get('user_id')
        except:
            return None
    return None

# Wrapper for API routes requiring auth
def api_auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'OPTIONS':
            return f(*args, **kwargs)
        user_id = check_auth_token()
        if not user_id:
             return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- Routes: Admin API ---

@app.route('/api/stats', methods=['GET'])
@api_auth_required
def api_stats():
    total_tokens = Token.query.count()
    active_tokens = Token.query.filter_by(is_active=True).count()
    # Mocking today stats for now or deriving from logs if detailed
    total_images = Token.query.with_entities(db.func.sum(Token.image_count)).scalar() or 0
    total_videos = Token.query.with_entities(db.func.sum(Token.video_count)).scalar() or 0
    total_errors = Token.query.with_entities(db.func.sum(Token.error_count)).scalar() or 0
    
    return jsonify({
        'total_tokens': total_tokens,
        'active_tokens': active_tokens,
        'today_images': 0, # Implement daily stats if needed
        'total_images': total_images,
        'today_videos': 0,
        'total_videos': total_videos,
        'today_errors': 0,
        'total_errors': total_errors
    })

@app.route('/api/tokens', methods=['GET'])
@api_auth_required
def get_tokens():
    tokens = Token.query.all()
    result = []
    for t in tokens:
        result.append({
            'id': t.id,
            'email': t.email,
            'is_active': t.is_active,
            'at_expires': t.at_expires.isoformat() if t.at_expires else None,
            'credits': t.credits,
            'user_paygate_tier': t.user_paygate_tier,
            'current_project_name': t.current_project_name,
            'current_project_id': t.current_project_id,
            'image_count': t.image_count,
            'video_count': t.video_count,
            'error_count': t.error_count,
            'remark': t.remark,
            'image_enabled': t.image_enabled,
            'video_enabled': t.video_enabled,
            'image_concurrency': t.image_concurrency,
            'video_concurrency': t.video_concurrency,
            'st': t.discord_token[:10] + '...' # Masked for security? Frontend uses it for edit.
            # Ideally return full ST for edit, or handle separately. Frontend calls edit and pre-fills ST.
            # Let's return full ST for now as admin panel.
        })
        # Add full ST if requested or for admin
        result[-1]['st'] = t.discord_token 
    return jsonify(result)

@app.route('/api/tokens', methods=['POST'])
@api_auth_required
def add_token():
    data = request.json
    st = data.get('st')
    if not st:
        return jsonify({'success': False, 'message': 'Missing Discord Token'}), 400
        
    token = Token(
        discord_token=st,
        remark=data.get('remark'),
        current_project_id=data.get('project_id'),
        current_project_name=data.get('project_name'),
        image_enabled=data.get('image_enabled', True),
        video_enabled=data.get('video_enabled', True),
        image_concurrency=data.get('image_concurrency', -1),
        video_concurrency=data.get('video_concurrency', -1)
    )
    db.session.add(token)
    db.session.commit()
    
    # Initial refresh
    success, msg = services.update_token_info(token.id)
    if not success:
        token.remark = f"Initial refresh failed: {msg}"
        db.session.commit()
        return jsonify({'success': True, 'message': 'Token added but refresh failed: ' + msg})
        
    return jsonify({'success': True})

@app.route('/api/tokens/<int:id>', methods=['PUT'])
@api_auth_required
def update_token(id):
    token = Token.query.get_or_404(id)
    data = request.json
    
    if 'st' in data: token.discord_token = data['st']
    if 'remark' in data: token.remark = data['remark']
    if 'project_id' in data: token.current_project_id = data['project_id']
    if 'project_name' in data: token.current_project_name = data['project_name']
    if 'image_enabled' in data: token.image_enabled = data['image_enabled']
    if 'video_enabled' in data: token.video_enabled = data['video_enabled']
    if 'image_concurrency' in data: token.image_concurrency = data['image_concurrency']
    if 'video_concurrency' in data: token.video_concurrency = data['video_concurrency']
    
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/<int:id>', methods=['DELETE'])
@api_auth_required
def delete_token(id):
    token = Token.query.get_or_404(id)
    db.session.delete(token)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/<int:id>/refresh-at', methods=['POST'])
@api_auth_required
def refresh_token_at(id):
    success, msg = services.update_token_info(id)
    if success:
        token = Token.query.get(id)
        return jsonify({'success': True, 'token': {'at_expires': token.at_expires.isoformat() if token.at_expires else None}})
    return jsonify({'success': False, 'detail': msg})

@app.route('/api/tokens/<int:id>/refresh-credits', methods=['POST'])
@api_auth_required
def refresh_token_credits(id):
    # This requires an API call to Zai using the AT
    # services.update_token_info gets the AT. We need another function to fetch credits.
    # For now, let's reuse update_token_info as it fetches account info if we implemented it fully.
    # But currently update_token_info only does login.
    # We need to implement credit fetching.
    # For now, stub it or just call update_token_info.
    success, msg = services.update_token_info(id)
    if success:
        token = Token.query.get(id)
        return jsonify({'success': True, 'credits': token.credits})
    return jsonify({'success': False, 'detail': msg})

@app.route('/api/tokens/st2at', methods=['POST'])
@api_auth_required
def st2at():
    data = request.json
    st = data.get('st')
    handler = services.get_zai_handler()
    result = handler.backend_login(st)
    if 'error' in result:
        return jsonify({'success': False, 'message': result['error']})
    return jsonify({'success': True, 'access_token': result.get('token')})

@app.route('/api/tokens/<int:id>/enable', methods=['POST'])
@api_auth_required
def enable_token(id):
    token = Token.query.get_or_404(id)
    token.is_active = True
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/<int:id>/disable', methods=['POST'])
@api_auth_required
def disable_token(id):
    token = Token.query.get_or_404(id)
    token.is_active = False
    db.session.commit()
    return jsonify({'success': True})

# --- Admin Config Routes ---

@app.route('/api/admin/config', methods=['GET', 'POST'])
@api_auth_required
def admin_config():
    config = SystemConfig.query.first()
    if request.method == 'GET':
        return jsonify({
            'error_ban_threshold': config.error_ban_threshold,
            'error_retry_count': config.error_retry_count,
            'admin_username': config.admin_username,
            'api_key': config.api_key,
            'debug_enabled': config.debug_enabled,
            'token_refresh_interval': config.token_refresh_interval
        })
    else:
        data = request.json
        if 'error_ban_threshold' in data: config.error_ban_threshold = data['error_ban_threshold']
        if 'error_retry_count' in data: config.error_retry_count = data['error_retry_count']
        
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/admin/debug', methods=['POST'])
@api_auth_required
def admin_debug():
    data = request.json
    config = SystemConfig.query.first()
    if 'enabled' in data: config.debug_enabled = data.get('enabled')
    if 'token_refresh_interval' in data: 
        config.token_refresh_interval = data.get('token_refresh_interval')
        try:
            scheduler.reschedule_job('token_refresher', trigger='interval', seconds=config.token_refresh_interval)
        except Exception as e:
            logger.error(f"Failed to reschedule job: {e}")
            
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/proxy/config', methods=['GET', 'POST'])
@api_auth_required
def proxy_config():
    config = SystemConfig.query.first()
    if request.method == 'GET':
        return jsonify({
            'proxy_enabled': config.proxy_enabled,
            'proxy_url': config.proxy_url
        })
    else:
        data = request.json
        if 'proxy_enabled' in data: config.proxy_enabled = data['proxy_enabled']
        if 'proxy_url' in data: config.proxy_url = data['proxy_url']
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/logs', methods=['GET'])
@api_auth_required
def get_logs():
    limit = request.args.get('limit', 100, type=int)
    logs = RequestLog.query.order_by(RequestLog.created_at.desc()).limit(limit).all()
    return jsonify([{
        'operation': l.operation,
        'token_email': l.token_email,
        'status_code': l.status_code,
        'duration': l.duration,
        'created_at': l.created_at.isoformat()
    } for l in logs])

@app.route('/api/cache/config', methods=['GET', 'POST'])
@api_auth_required
def cache_config():
    config = SystemConfig.query.first()
    if request.method == 'GET':
        return jsonify({'success': True, 'config': {
            'enabled': config.cache_enabled,
            'timeout': config.cache_timeout,
            'base_url': config.cache_base_url,
            'effective_base_url': config.cache_base_url or request.host_url
        }})
    else:
        # The frontend calls separate endpoints for enabled/timeout/base-url
        data = request.json
        if 'timeout' in data: config.cache_timeout = data['timeout']
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/cache/enabled', methods=['POST'])
@api_auth_required
def cache_enabled():
    data = request.json
    config = SystemConfig.query.first()
    config.cache_enabled = data.get('enabled')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/cache/base-url', methods=['POST'])
@api_auth_required
def cache_base_url():
    data = request.json
    config = SystemConfig.query.first()
    config.cache_base_url = data.get('base_url')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/generation/timeout', methods=['GET', 'POST'])
@api_auth_required
def generation_timeout():
    config = SystemConfig.query.first()
    if request.method == 'GET':
         return jsonify({'success': True, 'config': {
            'image_timeout': config.image_timeout,
            'video_timeout': config.video_timeout
        }})
    else:
        data = request.json
        config.image_timeout = data.get('image_timeout')
        config.video_timeout = data.get('video_timeout')
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/token-refresh/config', methods=['GET'])
@api_auth_required
def token_refresh_config():
    config = SystemConfig.query.first()
    return jsonify({'success': True, 'config': {
        'at_auto_refresh_enabled': config.at_auto_refresh_enabled
    }})
    
@app.route('/api/token-refresh/enabled', methods=['POST'])
@api_auth_required
def token_refresh_enabled():
    data = request.json
    config = SystemConfig.query.first()
    config.at_auto_refresh_enabled = data.get('enabled')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/import', methods=['POST'])
@api_auth_required
def import_tokens():
    data = request.json
    tokens_data = data.get('tokens', [])
    added = 0
    updated = 0
    
    for t_data in tokens_data:
        st = t_data.get('session_token')
        if not st: continue
        
        token = Token.query.filter_by(discord_token=st).first()
        if token:
            # Update
            token.email = t_data.get('email', token.email)
            token.zai_token = t_data.get('access_token', token.zai_token)
            token.is_active = t_data.get('is_active', token.is_active)
            token.image_enabled = t_data.get('image_enabled', True)
            token.video_enabled = t_data.get('video_enabled', True)
            updated += 1
        else:
            # Add
            token = Token(
                discord_token=st,
                email=t_data.get('email'),
                zai_token=t_data.get('access_token'),
                is_active=t_data.get('is_active', True),
                image_enabled=t_data.get('image_enabled', True),
                video_enabled=t_data.get('video_enabled', True),
                image_concurrency=t_data.get('image_concurrency', -1),
                video_concurrency=t_data.get('video_concurrency', -1)
            )
            db.session.add(token)
            added += 1
            
    db.session.commit()
    return jsonify({'success': True, 'added': added, 'updated': updated})

@app.route('/api/tokens/<int:id>/test', methods=['POST'])
@api_auth_required
def test_token(id):
    # Test by refreshing
    success, msg = services.update_token_info(id)
    token = Token.query.get(id)
    if success:
        return jsonify({
            'success': True, 
            'status': 'success', 
            'email': token.email,
            'sora2_supported': token.sora2_supported,
            'sora2_total_count': token.sora2_total_count,
            'sora2_redeemed_count': token.sora2_redeemed_count,
            'sora2_remaining_count': token.sora2_remaining_count
        })
    return jsonify({'success': False, 'message': msg})

@app.route('/api/tokens/<int:id>/sora2/activate', methods=['POST'])
@api_auth_required
def activate_sora2(id):
    # Not supported by zai_token.py yet
    return jsonify({'success': False, 'message': 'Not implemented in backend'})

# --- OpenAI Compatible Proxy ---

def select_token():
    # Simple Round Robin or Random
    # For now, just pick the first active one that has AT
    import random
    tokens = Token.query.filter_by(is_active=True).all()
    valid_tokens = [t for t in tokens if t.zai_token and not t.zai_token.startswith('SESSION')]
    if not valid_tokens:
        return None
    return random.choice(valid_tokens)

@app.route('/v1/chat/completions', methods=['POST'])
def proxy_chat_completions():
    start_time = time.time()
    
    # Verify API Key
    config = SystemConfig.query.first()
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer ') or auth_header.split(' ')[1] != config.api_key:
         return jsonify({'error': 'Invalid API Key'}), 401
         
    token = select_token()
    if not token:
        return jsonify({'error': 'No active tokens available'}), 503
        
    # Proxy to Zai
    zai_url = "https://zai.is/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {token.zai_token}",
        "Content-Type": "application/json"
    }
    
    try:
        resp = requests.post(zai_url, json=request.json, headers=headers, stream=True)
        
        # Log request
        duration = time.time() - start_time
        log = RequestLog(
            operation="chat/completions",
            token_email=token.email,
            status_code=resp.status_code,
            duration=duration
        )
        db.session.add(log)
        db.session.commit()
        
        if resp.status_code >= 400:
             # Handle errors, maybe ban token if too many
             token.error_count += 1
             if token.error_count >= config.error_ban_threshold:
                 token.is_active = False
                 token.remark = f"Auto-banned due to errors: {resp.text[:50]}"
             db.session.commit()
             return Response(resp.content, status=resp.status_code, mimetype='application/json')
             
        # Stream response
        def generate():
            for chunk in resp.iter_content(chunk_size=1024):
                if chunk:
                    yield chunk
        return Response(generate(), headers=dict(resp.headers))
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/v1/models', methods=['GET'])
def proxy_models():
    # Proxy or return static
    # Verify API Key
    config = SystemConfig.query.first()
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer ') or auth_header.split(' ')[1] != config.api_key:
         return jsonify({'error': 'Invalid API Key'}), 401
         
    token = select_token()
    if not token: # If no token, maybe we can't fetch models? Or just return default list.
        # Fallback list
        return jsonify({
            "object": "list",
            "data": [
                {"id": "gpt-4", "object": "model", "created": 1687882411, "owned_by": "openai"},
                {"id": "gpt-3.5-turbo", "object": "model", "created": 1677610602, "owned_by": "openai"}
            ]
        })
        
    zai_url = "https://zai.is/api/v1/models"
    headers = {
        "Authorization": f"Bearer {token.zai_token}"
    }
    try:
        resp = requests.get(zai_url, headers=headers)
        return Response(resp.content, status=resp.status_code, mimetype='application/json')
    except:
        return jsonify({"error": "Failed to fetch models"}), 500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False) # use_reloader=False for scheduler
