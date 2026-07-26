"""
手机号验证码登录系统
技术栈: Flask + 腾讯云短信 + SQLite
"""

import os
import re
import time
import random
import string
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, g
)

# ============ 腾讯云短信 SDK ============
try:
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.sms.v20210111 import sms_client, models as sms_models
    SMS_AVAILABLE = True
except ImportError:
    SMS_AVAILABLE = False
    print('[警告] 腾讯云SDK未安装，短信功能不可用。请执行: pip install tencentcloud-sdk-python')

# ============ 配置（全部从环境变量读取） ============
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# 腾讯云短信配置
TENCENT_SECRET_ID = os.environ.get('TENCENT_SECRET_ID', '')
TENCENT_SECRET_KEY = os.environ.get('TENCENT_SECRET_KEY', '')
SMS_SDK_APP_ID = os.environ.get('SMS_SDK_APP_ID', '')       # 短信应用ID
SMS_SIGN_NAME = os.environ.get('SMS_SIGN_NAME', '')          # 短信签名
SMS_TEMPLATE_ID = os.environ.get('SMS_TEMPLATE_ID', '')      # 短信模板ID

# 数据库路径
DB_PATH = os.environ.get('DB_PATH', 'users.db')

# 验证码配置
CODE_EXPIRE_MINUTES = 5      # 验证码有效期（分钟）
CODE_RESEND_SECONDS = 60     # 重新发送间隔（秒）
CODE_LENGTH = 6              # 验证码位数

# ============ 数据库操作 ============

def get_db():
    """获取当前请求的数据库连接"""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """初始化数据库表"""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            last_login TEXT,
            login_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS verification_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            code TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_codes_phone ON verification_codes(phone, used);
        CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
    ''')
    conn.commit()
    conn.close()


# ============ 腾讯云短信发送 ============

def send_sms_code(phone, code):
    """
    通过腾讯云短信API发送验证码
    返回: (success: bool, message: str)
    """
    if not SMS_AVAILABLE:
        return False, '短信SDK未安装'

    if not all([TENCENT_SECRET_ID, TENCENT_SECRET_KEY, SMS_SDK_APP_ID, SMS_SIGN_NAME, SMS_TEMPLATE_ID]):
        return False, '短信配置不完整，请检查环境变量'

    try:
        cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)

        http_profile = HttpProfile()
        http_profile.reqMethod = "POST"
        http_profile.endpoint = "sms.tencentcloudapi.com"

        client_profile = ClientProfile()
        client_profile.signMethod = "HmacSHA256"
        client_profile.httpProfile = http_profile

        client = sms_client.SmsClient(cred, "ap-guangzhou", client_profile)

        req = sms_models.SendSmsRequest()
        req.SmsSdkAppId = SMS_SDK_APP_ID
        req.SignName = SMS_SIGN_NAME
        req.TemplateId = SMS_TEMPLATE_ID
        req.TemplateParamSet = [code, str(CODE_EXPIRE_MINUTES)]
        req.PhoneNumberSet = [f"+86{phone}"]

        resp = client.SendSms(req)

        # 检查发送结果
        if resp.SendStatusSet and len(resp.SendStatusSet) > 0:
            status = resp.SendStatusSet[0]
            if status.Code == "Ok":
                return True, '发送成功'
            else:
                return False, f'发送失败: {status.Message}'

        return False, '未知错误'

    except Exception as e:
        return False, f'短信发送异常: {str(e)}'


# ============ 验证码逻辑 ============

def generate_code():
    """生成6位随机数字验证码"""
    return ''.join(random.choices(string.digits, k=CODE_LENGTH))


def check_rate_limit(phone):
    """
    检查发送频率限制
    返回: (allowed: bool, wait_seconds: int)
    """
    db = get_db()
    row = db.execute(
        'SELECT created_at FROM verification_codes WHERE phone = ? ORDER BY created_at DESC LIMIT 1',
        (phone,)
    ).fetchone()

    if row:
        elapsed = time.time() - row['created_at']
        if elapsed < CODE_RESEND_SECONDS:
            return False, int(CODE_RESEND_SECONDS - elapsed)

    return True, 0


def store_code(phone, code):
    """存储验证码到数据库"""
    db = get_db()
    now = time.time()
    expires = now + CODE_EXPIRE_MINUTES * 60

    db.execute(
        'INSERT INTO verification_codes (phone, code, created_at, expires_at, used) VALUES (?, ?, ?, ?, 0)',
        (phone, code, now, expires)
    )
    db.commit()


def verify_code(phone, code):
    """
    校验验证码
    返回: (valid: bool, message: str)
    """
    db = get_db()
    now = time.time()

    row = db.execute(
        '''SELECT id, code, expires_at FROM verification_codes
           WHERE phone = ? AND used = 0
           ORDER BY created_at DESC LIMIT 1''',
        (phone,)
    ).fetchone()

    if not row:
        return False, '验证码不存在，请重新获取'

    if now > row['expires_at']:
        return False, '验证码已过期，请重新获取'

    if row['code'] != code:
        return False, '验证码错误'

    # 标记为已使用
    db.execute('UPDATE verification_codes SET used = 1 WHERE id = ?', (row['id'],))
    db.commit()

    return True, '验证成功'


# ============ 用户管理 ============

def get_or_create_user(phone):
    """获取或创建用户（首次登录自动注册）"""
    db = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    user = db.execute('SELECT * FROM users WHERE phone = ?', (phone,)).fetchone()

    if user:
        # 更新登录信息
        db.execute(
            'UPDATE users SET last_login = ?, login_count = login_count + 1 WHERE id = ?',
            (now, user['id'])
        )
        db.commit()
        return dict(user)
    else:
        # 创建新用户
        cursor = db.execute(
            'INSERT INTO users (phone, created_at, last_login, login_count) VALUES (?, ?, ?, 1)',
            (phone, now, now)
        )
        db.commit()
        return {
            'id': cursor.lastrowid,
            'phone': phone,
            'created_at': now,
            'last_login': now,
            'login_count': 1
        }


# ============ 登录装饰器 ============

def login_required(f):
    """要求登录的路由装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': '未登录', 'redirect': '/login'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


# ============ 工具函数 ============

def validate_phone(phone):
    """验证手机号格式（中国大陆11位）"""
    pattern = r'^1[3-9]\d{9}$'
    return bool(re.match(pattern, phone))


def mask_phone(phone):
    """手机号脱敏: 138****1234"""
    if len(phone) == 11:
        return phone[:3] + '****' + phone[7:]
    return phone


# ============ 路由 ============

@app.route('/')
@login_required
def index():
    """首页（需要登录）"""
    return render_template('index.html')


@app.route('/login')
def login_page():
    """登录页面"""
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/api/send-code', methods=['POST'])
def api_send_code():
    """发送验证码接口"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '请求格式错误'}), 400

    phone = data.get('phone', '').strip()

    # 验证手机号
    if not validate_phone(phone):
        return jsonify({'success': False, 'message': '请输入正确的11位手机号'}), 400

    # 检查发送频率
    allowed, wait = check_rate_limit(phone)
    if not allowed:
        return jsonify({
            'success': False,
            'message': f'发送太频繁，请{wait}秒后重试'
        }), 429

    # 生成验证码
    code = generate_code()

    # 发送短信
    success, msg = send_sms_code(phone, code)
    if not success:
        # 开发模式：如果短信发送失败，在控制台打印验证码（仅开发环境）
        if os.environ.get('FLASK_ENV') == 'development':
            print(f'[开发模式] 手机号 {phone} 的验证码: {code}')
            # 开发模式下仍然存储验证码，允许测试
            store_code(phone, code)
            return jsonify({
                'success': True,
                'message': f'验证码已发送（开发模式: {code}）'
            })
        return jsonify({'success': False, 'message': msg}), 500

    # 存储验证码
    store_code(phone, code)

    return jsonify({
        'success': True,
        'message': f'验证码已发送至 {mask_phone(phone)}，{CODE_EXPIRE_MINUTES}分钟内有效'
    })


@app.route('/api/login', methods=['POST'])
def api_login():
    """登录验证接口"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '请求格式错误'}), 400

    phone = data.get('phone', '').strip()
    code = data.get('code', '').strip()

    if not validate_phone(phone):
        return jsonify({'success': False, 'message': '手机号格式错误'}), 400

    if not code or len(code) != CODE_LENGTH:
        return jsonify({'success': False, 'message': f'请输入{CODE_LENGTH}位验证码'}), 400

    # 校验验证码
    valid, msg = verify_code(phone, code)
    if not valid:
        return jsonify({'success': False, 'message': msg}), 400

    # 获取或创建用户
    user = get_or_create_user(phone)

    # 创建会话
    session.permanent = True
    session['user_id'] = user['id']
    session['phone'] = user['phone']
    session['login_time'] = datetime.now().isoformat()

    return jsonify({
        'success': True,
        'message': '登录成功',
        'redirect': '/'
    })


@app.route('/api/logout', methods=['POST'])
@login_required
def api_logout():
    """退出登录"""
    session.clear()
    return jsonify({'success': True, 'message': '已退出登录'})


@app.route('/api/user-info')
@login_required
def api_user_info():
    """获取当前用户信息"""
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not user:
        session.clear()
        return jsonify({'error': '用户不存在'}), 404

    return jsonify({
        'id': user['id'],
        'phone': mask_phone(user['phone']),
        'created_at': user['created_at'],
        'last_login': user['last_login'],
        'login_count': user['login_count']
    })


@app.route('/health')
def health():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'sms_available': SMS_AVAILABLE,
        'database': DB_PATH
    })


# ============ 启动 ============

if __name__ == '__main__':
    init_db()
    debug = os.environ.get('FLASK_ENV') == 'development'
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
