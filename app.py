# -*- coding: utf-8 -*-
"""
臻品汇数码奢品商城 - 主程序
技术栈: Python Flask + SQLite + 原生JavaScript + Bootstrap5

启动方式: python app.py
数据库:   详见 db.py（SQLite，文件默认生成在项目根目录 supermarket.db）
默认管理员: admin / admin123

功能模块:
  1. 用户登录/注册/鉴权 (PBKDF2-SHA256 密码哈希)
  2. 商品展示首页
  3. 后台管理 - 上新品 / 编辑 / 下架(软删除) / 快捷补货 / 库存流水 (仅管理员)
  4. 后台订单管理 - 全部订单列表、详情、状态流转 (仅管理员)
  5. 购物车系统 (Ajax 异步操作)
  6. 结账系统 (条件更新防超卖 + 库存流水 + 事务保护)
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from functools import wraps
from werkzeug.utils import secure_filename
import hashlib
import math
import os
import uuid
from datetime import datetime

# 数据库访问层（SQLite 适配，详见 db.py）
from db import (get_db, create_tables, seed_products, log_stock_change,
                CATEGORIES, guess_category)

app = Flask(__name__)

# Session 加密密钥。
# 生产环境必须通过环境变量 SECRET_KEY 指定一个固定值，否则每次重启
# 都会重新生成，导致所有用户被强制登出。本地开发时退回随机值即可。
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(24)
if not os.environ.get('SECRET_KEY'):
    print('[警告] 未设置 SECRET_KEY 环境变量，本次使用随机密钥（重启后登录状态会失效）')

# 图片上传配置
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ==================== 密码哈希工具函数 ====================

def hash_password(password):
    """
    使用 PBKDF2-SHA256 算法对密码进行哈希加密。
    PBKDF2 通过多次迭代和随机盐值来抵御彩虹表与暴力破解攻击。

    参数:
        password: 明文密码字符串
    返回:
        "salt_hex:key_hex" 格式的哈希字符串，用于存储到数据库
    """
    salt = os.urandom(32)                     # 生成 32 字节的随机盐
    key = hashlib.pbkdf2_hmac(
        'sha256',                              # 底层哈希算法
        password.encode('utf-8'),              # 将密码转为字节序列
        salt,                                  # 随机盐
        100000                                 # 迭代次数（10万次，平衡安全性与性能）
    )
    # 将盐和密钥以十六进制字符串形式拼接存储
    return salt.hex() + ':' + key.hex()


def verify_password(password, stored_password):
    """
    验证用户输入的密码是否与数据库中存储的哈希匹配。

    参数:
        password:         用户输入的明文密码
        stored_password:  数据库中存储的 "salt_hex:key_hex" 格式哈希
    返回:
        True 表示密码正确, False 表示密码错误
    """
    try:
        salt_hex, key_hex = stored_password.split(':')
        salt = bytes.fromhex(salt_hex)
        stored_key = bytes.fromhex(key_hex)
        # 使用相同的盐和迭代参数重新计算哈希
        new_key = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt,
            100000
        )
        return new_key == stored_key
    except (ValueError, AttributeError):
        # 若存储的哈希格式异常，直接返回 False
        return False


# ==================== 鉴权装饰器 ====================

def current_url():
    """
    当前请求的完整地址，且**不带** request.full_path 那个多余的 '?'。

    request.full_path 即使没有查询串也会在结尾补一个 '?'，
    直接拿去当 next 参数会生成 /login?next=/checkout? 这种难看的地址。
    """
    query = request.query_string.decode('utf-8')
    return request.path + ('?' + query if query else '')


@app.context_processor
def inject_template_helpers():
    """把 current_url 暴露给所有模板（base.html 的登录链接要用它带 next）。"""
    return {'current_url': current_url}


def safe_next_url(target):
    """
    校验登录后的回跳地址，防止开放重定向。

    只接受站内的绝对路径：必须以单个 '/' 开头。
    这样能挡掉 '//evil.com'（协议相对 URL，浏览器会当成外站）、
    'https://evil.com' 以及 '/\\evil.com' 这类绕过写法。
    校验不通过就返回 None，调用方回退到首页。
    """
    if not target:
        return None
    if not target.startswith('/') or target.startswith('//') or '\\' in target:
        return None
    return target


def _is_ajax_request():
    """判断当前请求是否来自前端 fetch/Ajax（需要 JSON 响应而不是重定向）。"""
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.path.startswith('/api/')
            or request.headers.get('Accept') == 'application/json'
            or request.is_json)


def login_required(f):
    """
    登录验证装饰器。
    若用户未登录：
      - AJAX 请求（X-Requested-With 或 /api/ 路径）→ 返回 JSON 401
      - 普通请求 → 重定向到登录页
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if _is_ajax_request():
                return jsonify({'success': False, 'message': '请先登录', 'redirect': url_for('login')}), 401
            # 带上当前地址，登录后能回到用户本来想去的页面
            return redirect(url_for('login', next=current_url()))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """
    管理员权限验证装饰器。

    未登录 / 非管理员时的响应必须区分 Ajax 与普通请求：
      原先这里无条件 redirect 到登录页，对 fetch 来说是致命的 ——
      fetch 会默默跟随 302 拿到登录页的 HTML，前端 r.json() 抛错，
      界面只显示「网络错误，请稍后重试」，用户永远不知道该去登录。
      改成和 login_required 一样返回 JSON 401，前端才能跳转到登录页。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if _is_ajax_request():
                return jsonify({'success': False, 'message': '请先登录', 'redirect': url_for('login')}), 401
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            if _is_ajax_request():
                return jsonify({'success': False, 'message': '权限不足：仅管理员可操作'}), 403
            return render_template('error.html',
                                   code=403,
                                   message='权限不足：仅管理员可访问此页面'), 403
        return f(*args, **kwargs)
    return decorated


# ==================== 商品字段校验 ====================

MAX_PRICE = 100000000      # 1 亿元，防止误输入天文数字
MAX_STOCK = 100000000      # 同理，且避免超出 SQLite 整数范围


def parse_price(value):
    """
    校验并解析商品价格，返回 (price, error)，error 为 None 表示通过。

    为什么要单独判 isfinite：
        `float('nan') <= 0` 的结果是 False，`float('inf') <= 0` 也是 False，
        所以原来只写 `if price <= 0` 是拦不住 nan / inf 的。
        一旦存进去，前台会显示「¥inf」，而 /api/product/<id> 会序列化出
        JSON 规范不允许的 Infinity，浏览器 JSON.parse 直接报错，
        编辑弹窗只会显示「网络错误」，很难排查。
    """
    try:
        price = float(value)
    except (ValueError, TypeError):
        return None, '请输入有效的商品价格'
    if not math.isfinite(price):
        return None, '请输入有效的商品价格'
    if price <= 0:
        return None, '商品价格必须大于 0'
    if price > MAX_PRICE:
        return None, '商品价格不能超过 {} 元'.format(MAX_PRICE)
    return round(price, 2), None


def parse_stock(value):
    """
    校验并解析库存数量，返回 (stock, error)。

    上限除了防误输入，也是为了避免超大的整数在绑定进 SQLite 时溢出报错。
    """
    try:
        stock = int(value)
    except (ValueError, TypeError):
        return None, '请输入有效的库存数量'
    if stock < 0:
        return None, '库存数量不能为负数'
    if stock > MAX_STOCK:
        return None, '库存数量不能超过 {}'.format(MAX_STOCK)
    return stock, None


# ==================== 数据库初始化 ====================

def init_db():
    """
    应用启动时自动检查并初始化数据库：
    1. 创建所有必需的表（如不存在）      —— 由 db.create_tables() 完成
    2. 插入示例商品（如商品表为空）      —— 由 db.seed_products() 完成
    3. 创建默认管理员账户 admin/admin123（如不存在）

    该函数是幂等的，重复调用不会重复插入数据，因此在应用启动时调用是安全的。
    """
    create_tables()
    seed_products()

    # 默认管理员账户需要用到本模块的密码哈希函数，所以放在这里而不是 db.py
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE username = %s', ('admin',))
    if not cursor.fetchone():
        hashed = hash_password('admin123')
        cursor.execute(
            'INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s)',
            ('admin', hashed, 1)
        )
        conn.commit()
        print('[初始化] 已创建默认管理员账户: admin / admin123')
    cursor.close()
    conn.close()
    print('[初始化] 数据库初始化完成！')


# ==================== 错误页 ====================

@app.errorhandler(404)
def page_not_found(e):
    """统一的 404 页面，替代 Werkzeug 默认的无样式英文报错页。"""
    return render_template('error.html', code=404, message='页面不存在'), 404


@app.errorhandler(500)
def internal_error(e):
    """
    统一的 500 页面。
    注意：这里不能直接访问数据库（原有连接可能已经因为异常而不可用），
    只渲染静态文案即可。
    """
    return render_template('error.html', code=500, message='服务器内部错误'), 500


# ==================== 页面路由 ====================

PER_PAGE = 12   # 首页每页商品数


@app.route('/')
def index():
    """
    首页 — 商品列表，支持关键词搜索、分类筛选与分页。

    query 参数：
        q     搜索词（匹配商品名称或描述）
        cat   分类 key（digital / luxury / lifestyle）
        page  页码，从 1 开始

    为什么筛选放在服务端而不是像原来那样用 JS 隐藏 DOM：
        前端 display:none 只能过滤「当前这一页已经渲染出来的」商品，
        一旦和搜索、分页组合就全乱了（第 2 页的数码商品根本不在 DOM 里）。
        放到 SQL 里，筛选 / 搜索 / 分页三者才能正确叠加。
    """
    keyword = request.args.get('q', '').strip()
    category = request.args.get('cat', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    where = ['is_active = 1']      # 已下架的商品对前台不可见
    params = []
    if category in CATEGORIES:
        where.append('category = %s')
        params.append(category)
    else:
        # 传了非法分类就当没筛选，避免整页空白让人以为商品丢了
        category = ''
    if keyword:
        where.append('(name LIKE %s OR description LIKE %s)')
        params.extend(['%{}%'.format(keyword)] * 2)

    where_sql = 'WHERE ' + ' AND '.join(where)

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) AS cnt FROM products {}'.format(where_sql), tuple(params))
    total_count = cursor.fetchone()['cnt']
    total_pages = max(1, (total_count + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)

    cursor.execute(
        'SELECT * FROM products {} ORDER BY id DESC LIMIT %s OFFSET %s'.format(where_sql),
        tuple(params) + (PER_PAGE, (page - 1) * PER_PAGE)
    )
    products = cursor.fetchall()
    # 将 Decimal 类型转为 float，便于模板中显示和 JSON 序列化
    for p in products:
        p['price'] = float(p['price'])

    # 各分类的商品数（不带搜索条件），用于分类标签上的角标
    cursor.execute(
        'SELECT category, COUNT(*) AS cnt FROM products WHERE is_active = 1 GROUP BY category'
    )
    category_counts = {row['category']: row['cnt'] for row in cursor.fetchall()}
    total_active = sum(category_counts.values())

    cursor.close()
    conn.close()

    return render_template(
        'index.html',
        products=products,
        categories=CATEGORIES,
        category_counts=category_counts,
        total_active=total_active,
        current_category=category,
        keyword=keyword,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
    )


# ==================== 用户登录 / 注册 / 退出 ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    用户登录页面。
    GET:  展示登录表单
    POST: 处理登录请求，严格区分"用户名不存在"和"密码错误"两种情况

    安全说明：
      故意区分了用户名不存在 vs 密码错误，这是产品需求（明确的用户提示）。
      在高安全性场景中，应统一提示"用户名或密码错误"以防止用户名枚举。
    """
    if request.method == 'GET':
        return render_template('login.html', next=safe_next_url(request.args.get('next')))

    # --- 处理登录表单提交 ---
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    next_url = safe_next_url(request.form.get('next'))

    def back(error=None, success=None):
        """重新渲染登录页，同时把 next 原样带回去，避免二次提交时丢失。"""
        return render_template('login.html', error=error, success=success, next=next_url)

    # 前端已有 required 验证，此处为后端兜底校验
    if not username or not password:
        return back(error='请输入用户名和密码')

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        # 第一步：查询用户是否存在
        cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
        user = cursor.fetchone()

        if not user:
            # 用户名不存在 → 给出明确错误提示
            return back(error='用户名不存在')

        # 第二步：使用 PBKDF2 验证密码是否匹配
        if not verify_password(password, user['password_hash']):
            # 密码错误 → 给出明确错误提示（与"用户名不存在"相区分）
            return back(error='密码错误')

        # 登录成功：将用户关键信息存入 Session
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['is_admin'] = bool(user['is_admin'])

        # 登录成功：回到用户原本想去的页面，没有就回首页
        return redirect(next_url or url_for('index'))

    except Exception as e:
        # 数据库连接失败、查询异常等意外错误 → 给出友好提示而非报错页面
        app.logger.error(f'登录异常: {e}')
        return back(error='登录服务暂时不可用，请稍后重试')
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    用户注册页面。
    GET:  展示注册表单
    POST: 校验输入、创建用户（密码PBKDF2加密存储）
    """
    if request.method == 'GET':
        return render_template('register.html')

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')

    # --- 后端表单校验 ---
    if not username or not password:
        return render_template('register.html', error='请填写所有必填字段')
    if password != confirm_password:
        return render_template('register.html', error='两次输入的密码不一致')
    if len(username) < 3:
        return render_template('register.html', error='用户名至少需要3个字符')
    if len(password) < 6:
        return render_template('register.html', error='密码至少需要6个字符')

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        # 检查用户名是否已被占用
        cursor.execute('SELECT id FROM users WHERE username = %s', (username,))
        if cursor.fetchone():
            return render_template('register.html', error='该用户名已被注册')

        # 创建新用户 — 密码使用 PBKDF2-SHA256 哈希加密后存储，绝不保存明文
        hashed = hash_password(password)
        cursor.execute(
            'INSERT INTO users (username, password_hash) VALUES (%s, %s)',
            (username, hashed)
        )
        conn.commit()

        # 注册成功 → 跳转到登录页
        return render_template('login.html', success='注册成功，请登录')

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'注册异常: {e}')
        return render_template('register.html', error='注册服务暂时不可用，请稍后重试')
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/logout')
def logout():
    """退出登录：清除所有 Session 数据"""
    session.clear()
    return redirect(url_for('index'))


# ==================== 后台管理（仅管理员） ====================

def _get_all_products():
    """
    辅助函数：查询后台商品列表，转换 Decimal → float。

    后台要看到全部商品（含已下架的），所以这里不加 is_active 过滤，
    而是把 is_active 带给模板，由模板显示「已下架」标记。
    上架中的排在前面。
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products ORDER BY is_active DESC, id DESC')
    products = cursor.fetchall()
    for p in products:
        p['price'] = float(p['price'])
    cursor.close()
    conn.close()
    return products


@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    """
    后台管理页面 — 上架新品 + 商品管理（编辑/下架）。
    仅管理员 (is_admin=1) 可访问，通过 @admin_required 装饰器保证。

    GET:  显示商品录入表单 + 全部商品管理列表
    POST: 校验表单数据，将新品写入 products 表
    """
    if request.method == 'GET':
        products = _get_all_products()
        return render_template('admin.html', products=products, categories=CATEGORIES)

    # --- 处理新品上架表单 ---
    name = request.form.get('name', '').strip()
    price_str = request.form.get('price', '').strip()
    stock_str = request.form.get('stock', '').strip()
    image_url = request.form.get('image_url', '').strip()
    description = request.form.get('description', '').strip()
    category = request.form.get('category', '').strip()

    # --- 后端数据校验 ---
    errors = []
    if not name:
        errors.append('请输入商品名称')

    price, price_error = parse_price(price_str)
    if price_error:
        errors.append(price_error)
    stock, stock_error = parse_stock(stock_str)
    if stock_error:
        errors.append(stock_error)
    if category and category not in CATEGORIES:
        errors.append('请选择有效的商品分类')

    if errors:
        products = _get_all_products()
        return render_template('admin.html', error='；'.join(errors),
                               products=products, categories=CATEGORIES)

    # 没选分类时按名称兜底猜一个，避免新商品全部堆进「生活」
    if not category:
        category = guess_category(name)

    # 校验通过：将商品数据插入数据库
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO products (name, price, stock, image_url, description, category) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (name, price, stock, image_url if image_url else '', description, category)
        )
        conn.commit()
        return redirect(url_for('admin'))
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'上架商品异常: {e}')
        products = _get_all_products()
        return render_template('admin.html', error='上架失败，请稍后重试', products=products)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ==================== 图片上传 API（仅管理员） ====================

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/api/upload', methods=['POST'])
@admin_required
def api_upload():
    """
    【Ajax】上传商品图片。
    接收 multipart/form-data，字段名: file
    返回 JSON: { success: true, url: "/static/uploads/xxx.jpg" }
    """
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未选择文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '未选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': '仅支持 JPG、PNG、GIF、WebP 格式'}), 400

    # 检查文件大小
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE:
        return jsonify({'success': False, 'message': '文件大小不能超过 5MB'}), 400

    # 生成唯一文件名：UUID + 原扩展名
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    try:
        file.save(filepath)
        url = f"/static/uploads/{filename}"
        return jsonify({'success': True, 'url': url, 'message': '上传成功'})
    except Exception as e:
        app.logger.error(f'图片上传异常: {e}')
        return jsonify({'success': False, 'message': '上传失败，请稍后重试'}), 500


# ==================== 商品管理 API（仅管理员） ====================

@app.route('/api/product/<int:product_id>', methods=['GET'])
@admin_required
def api_product_get(product_id):
    """
    【Ajax】获取单个商品信息，用于编辑弹窗预填数据。
    返回 JSON: { success: true, product: {...} }
    """
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM products WHERE id = %s', (product_id,))
        product = cursor.fetchone()
        if not product:
            return jsonify({'success': False, 'message': '商品不存在'}), 404
        product['price'] = float(product['price'])
        return jsonify({'success': True, 'product': product})
    except Exception as e:
        app.logger.error(f'获取商品异常: {e}')
        return jsonify({'success': False, 'message': '获取商品信息失败'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/api/product/<int:product_id>/update', methods=['POST'])
@admin_required
def api_product_update(product_id):
    """
    【Ajax】更新商品信息。
    请求体 JSON: { name, price, stock, image_url, description }
    响应 JSON:   { success: bool, message: str }
    """
    data = request.get_json()
    name = (data.get('name') or '').strip()
    price_str = data.get('price')
    stock_str = data.get('stock')
    image_url = (data.get('image_url') or '').strip()
    description = (data.get('description') or '').strip()
    category = (data.get('category') or '').strip()

    # 校验
    errors = []
    if not name:
        errors.append('请输入商品名称')
    if category and category not in CATEGORIES:
        errors.append('请选择有效的商品分类')
    price, price_error = parse_price(price_str)
    if price_error:
        errors.append(price_error)
    stock, stock_error = parse_stock(stock_str)
    if stock_error:
        errors.append(stock_error)

    if errors:
        return jsonify({'success': False, 'message': '；'.join(errors)})

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        # 先取原库存，用于判断这次编辑是否改动了库存、以及记录流水
        cursor.execute('SELECT name, stock FROM products WHERE id = %s', (product_id,))
        old = cursor.fetchone()
        if not old:
            return jsonify({'success': False, 'message': '商品不存在'}), 404

        cursor.execute(
            'UPDATE products SET name=%s, price=%s, stock=%s, image_url=%s, description=%s, category=%s WHERE id=%s',
            (name, price, stock, image_url, description, category or guess_category(name), product_id)
        )
        # 编辑弹窗里也能直接改库存，若改了就必须补一条流水，
        # 否则 stock_logs 的 stock_after 链条会断掉（上一条写着余 60，
        # 下一条下单却写着余 776），流水就失去对账意义了。
        if stock != old['stock']:
            log_stock_change(
                cursor, product_id, name, stock - old['stock'], stock,
                reason='manual',
                note='编辑商品改为 {} 件（原 {} 件）'.format(stock, old['stock']),
                operator=session.get('username', '')
            )
        conn.commit()
        return jsonify({'success': True, 'message': '商品已更新'})
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'更新商品异常: {e}')
        return jsonify({'success': False, 'message': '更新失败，请稍后重试'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/api/product/<int:product_id>/delete', methods=['POST'])
@admin_required
def api_product_delete(product_id):
    """
    【Ajax】下架商品（软删除：is_active 置 0）。

    为什么用软删除而不是 DELETE：
        order_items.product_id 是指向 products 的外键，且没有 ON DELETE 规则。
        商品只要被下过单，DELETE 就会触发 FOREIGN KEY constraint failed，
        前台表现为「下架失败，请稍后重试」，管理员再也下架不掉这件商品。
        改成 is_active=0 后，下架永远成功，且历史订单的商品快照完好无损。
    响应 JSON: { success: bool, message: str }
    """
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE products SET is_active = 0 WHERE id = %s AND is_active = 1',
            (product_id,)
        )
        if cursor.rowcount == 0:
            # 要么商品不存在，要么已经是下架状态 —— 两种都如实告诉管理员
            cursor.execute('SELECT is_active FROM products WHERE id = %s', (product_id,))
            row = cursor.fetchone()
            conn.rollback()
            if row is None:
                return jsonify({'success': False, 'message': '商品不存在'}), 404
            return jsonify({'success': False, 'message': '该商品已是下架状态'})
        conn.commit()
        return jsonify({'success': True, 'message': '商品已下架'})
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'下架商品异常: {e}')
        return jsonify({'success': False, 'message': '下架失败，请稍后重试'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/api/product/<int:product_id>/restore', methods=['POST'])
@admin_required
def api_product_restore(product_id):
    """
    【Ajax】重新上架（软删除的反向操作：is_active 置回 1）。
    响应 JSON: { success: bool, message: str }
    """
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE products SET is_active = 1 WHERE id = %s AND is_active = 0',
            (product_id,)
        )
        if cursor.rowcount == 0:
            cursor.execute('SELECT is_active FROM products WHERE id = %s', (product_id,))
            row = cursor.fetchone()
            conn.rollback()
            if row is None:
                return jsonify({'success': False, 'message': '商品不存在'}), 404
            return jsonify({'success': False, 'message': '该商品已在架'})
        conn.commit()
        return jsonify({'success': True, 'message': '商品已重新上架'})
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'重新上架异常: {e}')
        return jsonify({'success': False, 'message': '上架失败，请稍后重试'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/api/product/<int:product_id>/restock', methods=['POST'])
@admin_required
def api_product_restock(product_id):
    """
    【Ajax】快捷补货 —— 在现有库存基础上追加数量，并写入库存流水。

    请求体 JSON: { amount: int(必填, 正数), note: str(可选) }
    响应 JSON:   { success, message, stock }

    与「编辑商品」的区别：
        编辑是整条记录覆盖（name/price/image 一起重写，填错就把商品改坏）；
        补货只做 stock = stock + N 的增量更新，不碰其他字段，更安全。
    """
    data = request.get_json(silent=True) or {}
    note = (data.get('note') or '').strip()

    # --- 校验补货数量 ---
    try:
        amount = int(data.get('amount'))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': '请输入有效的补货数量'})
    if amount <= 0:
        return jsonify({'success': False, 'message': '补货数量必须大于 0'})
    if amount > 1000000:
        return jsonify({'success': False, 'message': '单次补货不能超过 1000000 件'})

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        # 先在事务内读出商品当前状态，避免「读到的库存」和「写入的库存」不一致
        cursor.execute('SELECT name, stock FROM products WHERE id = %s', (product_id,))
        product = cursor.fetchone()
        if not product:
            return jsonify({'success': False, 'message': '商品不存在'}), 404

        # 用 stock = stock + N 的增量写法，而不是先读后写再覆盖。
        # 读-改-写之间若另一个请求也在补货，后写的那次会覆盖掉前一次（丢更新）；
        # 交给数据库在同一条语句里做加法就不会丢。
        cursor.execute(
            'UPDATE products SET stock = stock + %s WHERE id = %s',
            (amount, product_id)
        )
        cursor.execute('SELECT stock FROM products WHERE id = %s', (product_id,))
        new_stock = cursor.fetchone()['stock']

        # 库存流水与库存变更在同一事务里，要么都成功要么都回滚
        log_stock_change(
            cursor, product_id, product['name'], amount, new_stock,
            reason='restock', note=note, operator=session.get('username', '')
        )
        conn.commit()
        return jsonify({
            'success': True,
            'message': '补货成功，{} 库存 {} → {}'.format(product['name'], product['stock'], new_stock),
            'stock': new_stock
        })
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'补货异常: {e}')
        return jsonify({'success': False, 'message': '补货失败，请稍后重试'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/api/product/<int:product_id>/stock_logs', methods=['GET'])
@admin_required
def api_product_stock_logs(product_id):
    """
    【Ajax】查询某商品的库存流水（最近 50 条）。
    响应 JSON: { success, logs: [{change_amount, stock_after, reason, note, operator, created_at}] }
    """
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT change_amount, stock_after, reason, note, operator, created_at
               FROM stock_logs
               WHERE product_id = %s
               ORDER BY id DESC
               LIMIT 50''',
            (product_id,)
        )
        logs = cursor.fetchall()
        reason_labels = {'restock': '补货', 'order': '售出', 'order_cancel': '取消回滚'}
        for log in logs:
            log['reason_label'] = reason_labels.get(log['reason'], log['reason'])
            # created_at 已被 db.py 的转换器还原成 datetime，这里格式化成字符串给前端
            if hasattr(log['created_at'], 'strftime'):
                log['created_at'] = log['created_at'].strftime('%Y-%m-%d %H:%M')
        return jsonify({'success': True, 'logs': logs})
    except Exception as e:
        app.logger.error(f'查询库存流水异常: {e}')
        return jsonify({'success': False, 'message': '查询库存流水失败'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ==================== 订单记录 ====================

@app.route('/orders')
@login_required
def orders():
    """
    我的订单页面 — 展示当前用户的全部订单。
    每个订单包含订单明细（商品快照）。
    """
    conn = get_db()
    cursor = conn.cursor()

    # 查询用户的所有订单，按时间倒序
    cursor.execute('''
        SELECT id, total_amount, status, address, payment_method, created_at
        FROM orders
        WHERE user_id = %s
        ORDER BY created_at DESC
    ''', (session['user_id'],))
    order_list = cursor.fetchall()

    # 为每个订单加载明细
    for order in order_list:
        order['total_amount'] = float(order['total_amount'])
        cursor.execute('''
            SELECT product_name, price, quantity
            FROM order_items
            WHERE order_id = %s
        ''', (order['id'],))
        order['items'] = cursor.fetchall()
        for item in order['items']:
            item['price'] = float(item['price'])

    cursor.close()
    conn.close()

    return render_template(
        'orders.html',
        orders=order_list,
        status_labels=ORDER_STATUS_LABELS
    )


def restore_order_stock(cursor, order_id, operator=''):
    """
    把订单内的商品数量加回库存，并为每一项写一条 order_cancel 流水。
    调用方负责 commit（通常与订单状态变更处于同一事务）。

    商品可能已被下架（软删除），但行还在，库存照样加得回去 ——
    这正是取消订单作为「补货兜底」能生效的原因。
    """
    cursor.execute(
        'SELECT product_id, product_name, quantity FROM order_items WHERE order_id = %s',
        (order_id,)
    )
    items = cursor.fetchall()
    for item in items:
        cursor.execute(
            'UPDATE products SET stock = stock + %s WHERE id = %s',
            (item['quantity'], item['product_id'])
        )
        cursor.execute('SELECT stock FROM products WHERE id = %s', (item['product_id'],))
        row = cursor.fetchone()
        if row is not None:
            log_stock_change(
                cursor, item['product_id'], item['product_name'],
                item['quantity'], row['stock'],
                reason='order_cancel',
                note='订单 #{} 取消回滚'.format(order_id),
                operator=operator
            )
    return len(items)


# 用户可以自行取消的状态。已发货之后就不能自己取消了，得联系客服/管理员。
USER_CANCELLABLE_STATUSES = ('pending', 'paid')


@app.route('/order/success/<int:order_id>')
@login_required
def order_success(order_id):
    """
    下单成功页（GET）。

    由 checkout 的 POST 重定向到这里，避免刷新页面重复提交订单。
    带 user_id 条件，防止通过改 URL 看到别人的订单金额。
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT total_amount FROM orders WHERE id = %s AND user_id = %s',
        (order_id, session['user_id'])
    )
    order = cursor.fetchone()
    cursor.close()
    conn.close()

    if not order:
        return render_template('error.html', code=404, message='订单不存在'), 404

    return render_template('order_success.html', order_id=order_id,
                           total=float(order['total_amount']))


@app.route('/order/<int:order_id>')
@login_required
def order_detail(order_id):
    """
    我的订单详情页。

    越权处理：查询同时带上 user_id 条件，别人的订单直接当作「不存在」返回 404，
    而不是 403 —— 403 会泄露「这个订单号是真实存在的」这一信息。
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT id, total_amount, status, address, payment_method, created_at
           FROM orders
           WHERE id = %s AND user_id = %s''',
        (order_id, session['user_id'])
    )
    order = cursor.fetchone()

    if not order:
        cursor.close()
        conn.close()
        return render_template('error.html', code=404, message='订单不存在'), 404

    order['total_amount'] = float(order['total_amount'])
    order['status_label'] = ORDER_STATUS_LABELS.get(order['status'], order['status'])

    cursor.execute(
        '''SELECT product_id, product_name, price, quantity
           FROM order_items WHERE order_id = %s ORDER BY id''',
        (order_id,)
    )
    items = cursor.fetchall()
    for item in items:
        item['price'] = float(item['price'])
        item['subtotal'] = round(item['price'] * item['quantity'], 2)

    cursor.close()
    conn.close()

    return render_template(
        'order_detail.html',
        order=order,
        items=items,
        can_cancel=order['status'] in USER_CANCELLABLE_STATUSES
    )


@app.route('/api/order/<int:order_id>/cancel', methods=['POST'])
@login_required
def api_order_cancel(order_id):
    """
    【Ajax】用户取消自己的订单。

    只允许取消 pending / paid（还没发货）的订单；取消时把商品数量加回库存。
    管理员侧的取消接口是 /api/admin/order/<id>/status，两边共用
    restore_order_stock()，保证回滚口径一致。
    """
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        # 同样带 user_id 条件，防止用户取消别人的订单
        cursor.execute(
            'SELECT id, status FROM orders WHERE id = %s AND user_id = %s',
            (order_id, session['user_id'])
        )
        order = cursor.fetchone()
        if not order:
            return jsonify({'success': False, 'message': '订单不存在'}), 404

        if order['status'] == 'cancelled':
            return jsonify({'success': False, 'message': '订单已取消，请勿重复操作'})
        if order['status'] not in USER_CANCELLABLE_STATUSES:
            return jsonify({
                'success': False,
                'message': '订单当前状态为「{}」，已发货的订单请联系客服处理'.format(
                    ORDER_STATUS_LABELS.get(order['status'], order['status'])
                )
            })

        restore_order_stock(cursor, order_id, session.get('username', ''))
        cursor.execute('UPDATE orders SET status = %s WHERE id = %s', ('cancelled', order_id))
        conn.commit()
        return jsonify({
            'success': True,
            'message': '订单 #{} 已取消，商品数量已加回库存'.format(order_id)
        })
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'用户取消订单异常: {e}')
        return jsonify({'success': False, 'message': '取消失败，请稍后重试'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ==================== 后台订单管理（仅管理员） ====================

# 订单状态中文名。前台 orders.html 也复用这套文案，避免两处写法不一致。
ORDER_STATUS_LABELS = {
    'pending': '待支付',
    'paid': '已支付',
    'shipped': '已发货',
    'completed': '已完成',
    'cancelled': '已取消',
}

# 允许的状态流转。只允许单向推进，已完成的订单不能再取消。
ORDER_STATUS_FLOW = {
    'pending':   ['paid', 'cancelled'],
    'paid':      ['shipped', 'cancelled'],
    'shipped':   ['completed', 'cancelled'],
    'completed': [],
    'cancelled': [],
}


@app.route('/admin/orders')
@admin_required
def admin_orders():
    """
    后台订单列表 — 展示全部用户的订单（这才是管理员该看到的视图）。

    与企业版前台的 /orders 不同：那个路由带 WHERE user_id = 当前用户 的过滤，
    管理员在那边只能看到自己下的单，看不到任何客户的订单。
    这里支持按状态筛选和按订单号/用户名搜索。
    """
    # --- 读取筛选参数 ---
    status = request.args.get('status', '').strip()
    keyword = request.args.get('q', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    per_page = 20

    where = []
    params = []
    if status in ORDER_STATUS_LABELS:
        where.append('o.status = %s')
        params.append(status)
    if keyword:
        # 订单号支持纯数字精确匹配，用户名支持模糊匹配。
        # 长度限制是必须的：SQLite 的整数绑定是 64 位，粘贴一长串数字（19 位以上）
        # 会让 int(keyword) 溢出并抛 OverflowError，整个页面 500。
        if keyword.isdigit() and len(keyword) <= 18:
            where.append('(u.username LIKE %s OR o.id = %s)')
            params.extend(['%{}%'.format(keyword), int(keyword)])
        else:
            where.append('u.username LIKE %s')
            params.append('%{}%'.format(keyword))

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    conn = get_db()
    cursor = conn.cursor()

    # 总条数（用于分页）
    cursor.execute(
        '''SELECT COUNT(*) AS cnt
           FROM orders o JOIN users u ON o.user_id = u.id
           {}'''.format(where_sql),
        tuple(params)
    )
    total_count = cursor.fetchone()['cnt']
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    cursor.execute(
        '''SELECT o.id, o.user_id, u.username, o.total_amount, o.status,
                  o.address, o.payment_method, o.created_at,
                  (SELECT COALESCE(SUM(quantity), 0) FROM order_items WHERE order_id = o.id) AS item_count
           FROM orders o
           JOIN users u ON o.user_id = u.id
           {}
           ORDER BY o.created_at DESC, o.id DESC
           LIMIT %s OFFSET %s'''.format(where_sql),
        tuple(params) + (per_page, (page - 1) * per_page)
    )
    order_list = cursor.fetchall()
    for o in order_list:
        o['total_amount'] = float(o['total_amount'])
        o['status_label'] = ORDER_STATUS_LABELS.get(o['status'], o['status'])

    # 各状态订单数，用于筛选标签上的角标
    cursor.execute('SELECT status, COUNT(*) AS cnt FROM orders GROUP BY status')
    status_counts = {row['status']: row['cnt'] for row in cursor.fetchall()}
    total_orders = sum(status_counts.values())

    cursor.close()
    conn.close()

    return render_template(
        'admin_orders.html',
        orders=order_list,
        status_labels=ORDER_STATUS_LABELS,
        status_counts=status_counts,
        total_orders=total_orders,
        current_status=status,
        keyword=keyword,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
    )


@app.route('/admin/order/<int:order_id>')
@admin_required
def admin_order_detail(order_id):
    """后台订单详情 — 订单主信息 + 商品明细 + 可执行的下一步状态。"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        '''SELECT o.id, o.user_id, u.username, o.total_amount, o.status,
                  o.address, o.payment_method, o.created_at
           FROM orders o
           JOIN users u ON o.user_id = u.id
           WHERE o.id = %s''',
        (order_id,)
    )
    order = cursor.fetchone()
    if not order:
        cursor.close()
        conn.close()
        return render_template('admin_order_detail.html', order=None, order_id=order_id), 404

    order['total_amount'] = float(order['total_amount'])
    order['status_label'] = ORDER_STATUS_LABELS.get(order['status'], order['status'])

    cursor.execute(
        '''SELECT product_id, product_name, price, quantity
           FROM order_items WHERE order_id = %s ORDER BY id''',
        (order_id,)
    )
    items = cursor.fetchall()
    for item in items:
        item['price'] = float(item['price'])
        item['subtotal'] = round(item['price'] * item['quantity'], 2)

    cursor.close()
    conn.close()

    return render_template(
        'admin_order_detail.html',
        order=order,
        items=items,
        status_labels=ORDER_STATUS_LABELS,
        next_statuses=ORDER_STATUS_FLOW.get(order['status'], []),
    )


@app.route('/api/admin/order/<int:order_id>/status', methods=['POST'])
@admin_required
def api_admin_order_status(order_id):
    """
    【Ajax】修改订单状态。

    请求体 JSON: { status: str }
    响应 JSON:   { success, message }

    取消订单时会把订单里的商品数量加回库存，并写一条 order_cancel 流水 ——
    这正是「商品卖完补不上货」的兜底：客户取消/退款后库存自动回滚，
    不需要管理员手动去记「刚才卖了几件」。
    """
    data = request.get_json(silent=True) or {}
    new_status = (data.get('status') or '').strip()

    if new_status not in ORDER_STATUS_LABELS:
        return jsonify({'success': False, 'message': '无效的订单状态'})

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, status FROM orders WHERE id = %s', (order_id,))
        order = cursor.fetchone()
        if not order:
            return jsonify({'success': False, 'message': '订单不存在'}), 404

        old_status = order['status']
        if new_status == old_status:
            return jsonify({'success': False, 'message': '订单已处于该状态'})
        if new_status not in ORDER_STATUS_FLOW.get(old_status, []):
            return jsonify({
                'success': False,
                'message': '不能从「{}」变更为「{}」'.format(
                    ORDER_STATUS_LABELS.get(old_status, old_status),
                    ORDER_STATUS_LABELS[new_status]
                )
            })

        # --- 取消订单：回滚库存 ---
        if new_status == 'cancelled':
            restore_order_stock(cursor, order_id, session.get('username', ''))

        cursor.execute(
            'UPDATE orders SET status = %s WHERE id = %s',
            (new_status, order_id)
        )
        conn.commit()
        return jsonify({
            'success': True,
            'message': '订单 #{} 已更新为「{}」'.format(
                order_id, ORDER_STATUS_LABELS[new_status]
            )
        })
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'修改订单状态异常: {e}')
        return jsonify({'success': False, 'message': '操作失败，请稍后重试'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ==================== 购物车页面 ====================

@app.route('/cart')
@login_required
def cart():
    """
    购物车页面。
    通过 JOIN 查询用户的购物车记录及对应商品信息，
    计算每项小计 (price × quantity) 和总价。
    """
    conn = get_db()
    cursor = conn.cursor()
    # 不过滤 is_active：已下架的商品仍要显示出来（打上「已下架」标记），
    # 否则用户会看到总价对不上，却找不到原因。真正的拦截在 checkout。
    cursor.execute('''
        SELECT
            c.id AS cart_id,
            c.quantity,
            p.id AS product_id,
            p.name,
            p.price,
            p.stock,
            p.image_url,
            p.is_active
        FROM carts c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = %s
    ''', (session['user_id'],))
    items = cursor.fetchall()

    # 转换 Decimal → float，计算小计
    for item in items:
        item['price'] = float(item['price'])
        item['subtotal'] = round(item['price'] * item['quantity'], 2)

    cursor.close()
    conn.close()

    # 计算购物车总金额
    total = round(sum(item['subtotal'] for item in items), 2)
    return render_template('cart.html', items=items, total=total)


# ==================== 结账页面 ====================

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    """
    结账页面。
    GET:  展示订单确认信息、收货地址和支付方式选择
    POST: 处理支付请求 — 库存检查、扣减库存、创建订单、清空购物车（事务保护）
    """
    conn = get_db()
    cursor = conn.cursor()

    # 获取当前用户的购物车内容
    cursor.execute('''
        SELECT
            c.id AS cart_id, c.quantity,
            p.id AS product_id, p.name, p.price, p.stock, p.image_url, p.is_active
        FROM carts c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = %s
    ''', (session['user_id'],))
    items = cursor.fetchall()

    # 购物车为空 → 重定向回购物车
    if not items:
        cursor.close()
        conn.close()
        return redirect(url_for('cart'))

    for item in items:
        item['price'] = float(item['price'])
        item['subtotal'] = round(item['price'] * item['quantity'], 2)

    total = round(sum(item['subtotal'] for item in items), 2)

    if request.method == 'GET':
        cursor.close()
        conn.close()
        return render_template('checkout.html', items=items, total=total)

    # ============================================================
    # POST: 处理支付（下单）请求
    # 整个下单流程使用事务保护，任何一步失败都会回滚
    # ============================================================
    address = request.form.get('address', '').strip()
    payment_method = request.form.get('payment_method', '')

    if not address:
        cursor.close()
        conn.close()
        return render_template('checkout.html', items=items, total=total, error='请输入收货地址')
    if not payment_method:
        cursor.close()
        conn.close()
        return render_template('checkout.html', items=items, total=total, error='请选择支付方式')

    try:
        # --- 第一步：检查商品是否仍在售 ---
        for item in items:
            cursor.execute(
                'SELECT name, stock, is_active FROM products WHERE id = %s',
                (item['product_id'],)
            )
            product = cursor.fetchone()
            if not product or not product['is_active']:
                conn.rollback()
                cursor.close()
                conn.close()
                return render_template(
                    'checkout.html', items=items, total=total,
                    error='商品“{}”已下架，请从购物车中移除后再结算'.format(
                        product['name'] if product else '未知商品'
                    )
                )

        # --- 第二步：逐项扣减库存 ---
        # 关键：把库存判断写进 UPDATE 的 WHERE 条件里（stock >= 购买数量），
        # 由数据库在一条语句内完成「判断 + 扣减」，而不是先 SELECT 再 UPDATE。
        # 原先的 SELECT ... FOR UPDATE 在 SQLite 上会被 db.py 直接删掉（SQLite
        # 没有行级锁），等于没有保护；改成条件更新后，即使两个请求并发，
        # 也只有一个能扣减成功，另一个 rowcount 为 0，从根上堵住超卖。
        for item in items:
            cursor.execute(
                'UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s',
                (item['quantity'], item['product_id'], item['quantity'])
            )
            if cursor.rowcount == 0:
                # 没扣到 → 说明库存在这一步之间被别的订单抢走了
                cursor.execute(
                    'SELECT name, stock FROM products WHERE id = %s',
                    (item['product_id'],)
                )
                fresh = cursor.fetchone()
                conn.rollback()
                cursor.close()
                conn.close()
                return render_template(
                    'checkout.html', items=items, total=total,
                    error='商品“{}”库存不足，当前仅剩 {} 件'.format(
                        fresh['name'] if fresh else item['name'],
                        fresh['stock'] if fresh else 0
                    )
                )
            # 记录出库流水（与扣减同处一个事务）
            cursor.execute(
                'SELECT stock FROM products WHERE id = %s', (item['product_id'],)
            )
            stock_after = cursor.fetchone()['stock']
            log_stock_change(
                cursor, item['product_id'], item['name'],
                -item['quantity'], stock_after,
                reason='order', note='订单出库', operator=session.get('username', '')
            )

        # --- 第三步：创建订单主记录 ---
        cursor.execute(
            '''INSERT INTO orders (user_id, total_amount, status, address, payment_method)
               VALUES (%s, %s, %s, %s, %s)''',
            (session['user_id'], total, 'paid', address, payment_method)
        )
        order_id = cursor.lastrowid

        # --- 第四步：保存订单明细（商品快照） ---
        for item in items:
            cursor.execute(
                '''INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
                   VALUES (%s, %s, %s, %s, %s)''',
                (order_id, item['product_id'], item['name'], item['price'], item['quantity'])
            )

        # --- 第五步：清空该用户的购物车 ---
        cursor.execute('DELETE FROM carts WHERE user_id = %s', (session['user_id'],))

        # 所有步骤成功 → 提交事务
        conn.commit()
        cursor.close()
        conn.close()

        # PRG（Post/Redirect/Get）：下单成功后重定向，而不是直接渲染成功页。
        # 直接渲染的话，用户在成功页按 F5 会重发刚才那个 POST，而购物车此时
        # 已经清空，于是被弹回 /cart，成功页凭空消失。
        return redirect(url_for('order_success', order_id=order_id))

    except Exception as e:
        # 任何异常 → 回滚事务，保证数据一致性
        conn.rollback()
        cursor.close()
        conn.close()
        return render_template(
            'checkout.html', items=items, total=total,
            error='下单失败，请稍后重试：{}'.format(str(e))
        )


# ==================== 购物车 API（Ajax 异步接口） ====================

@app.route('/api/cart/add', methods=['POST'])
@login_required
def api_cart_add():
    """
    【Ajax】加入购物车。

    请求体 JSON: { product_id: int, quantity: int(可选, 默认1) }
    响应 JSON:   { success: bool, message: str, cart_count: int }

    逻辑：若购物车中已有该商品则累加数量，否则新增记录。
    """
    data = request.get_json()
    product_id = data.get('product_id')
    quantity = data.get('quantity', 1)

    if not product_id:
        return jsonify({'success': False, 'message': '缺少商品ID'})

    try:
        quantity = int(quantity)
        if quantity < 1:
            quantity = 1
    except (ValueError, TypeError):
        quantity = 1

    conn = get_db()
    cursor = conn.cursor()

    # 验证商品是否存在且仍在售
    cursor.execute('SELECT id, stock, is_active FROM products WHERE id = %s', (product_id,))
    product = cursor.fetchone()
    if not product:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': '商品不存在'})
    if not product['is_active']:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': '该商品已下架'})

    # 检查是否已在购物车中
    cursor.execute(
        'SELECT id, quantity FROM carts WHERE user_id = %s AND product_id = %s',
        (session['user_id'], product_id)
    )
    cart_item = cursor.fetchone()

    if cart_item:
        # 已存在 → 累加数量
        new_qty = cart_item['quantity'] + quantity
        if new_qty > product['stock']:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'message': '库存不足，最多可添加 {} 件'.format(product['stock'])
            })
        cursor.execute(
            'UPDATE carts SET quantity = %s WHERE id = %s',
            (new_qty, cart_item['id'])
        )
    else:
        # 不存在 → 新增记录
        if quantity > product['stock']:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'message': '库存不足，最多可添加 {} 件'.format(product['stock'])
            })
        cursor.execute(
            'INSERT INTO carts (user_id, product_id, quantity) VALUES (%s, %s, %s)',
            (session['user_id'], product_id, quantity)
        )

    conn.commit()

    # 获取更新后的购物车总数量（用于导航栏角标）
    cursor.execute(
        'SELECT COALESCE(SUM(quantity), 0) AS count FROM carts WHERE user_id = %s',
        (session['user_id'],)
    )
    cart_count = cursor.fetchone()['count']

    cursor.close()
    conn.close()

    return jsonify({
        'success': True,
        'message': '已加入购物车',
        'cart_count': cart_count
    })


@app.route('/api/cart/update', methods=['POST'])
@login_required
def api_cart_update():
    """
    【Ajax】更新购物车商品数量。

    请求体 JSON: { product_id: int, quantity: int }
    响应 JSON:   { success, subtotal, quantity, total, cart_count }

    若 quantity <= 0，则删除该购物车项。
    """
    data = request.get_json()
    product_id = data.get('product_id')
    quantity = data.get('quantity')

    if not product_id or quantity is None:
        return jsonify({'success': False, 'message': '参数错误'})

    try:
        quantity = int(quantity)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': '数量格式不正确'})

    conn = get_db()
    cursor = conn.cursor()

    if quantity <= 0:
        # 数量 ≤ 0 → 删除该项
        cursor.execute(
            'DELETE FROM carts WHERE user_id = %s AND product_id = %s',
            (session['user_id'], product_id)
        )
    else:
        # 更新数量前检查库存上限
        cursor.execute('SELECT stock FROM products WHERE id = %s', (product_id,))
        product = cursor.fetchone()
        if product and quantity > product['stock']:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'message': '库存不足，最多可购买 {} 件'.format(product['stock'])
            })

        cursor.execute(
            'UPDATE carts SET quantity = %s WHERE user_id = %s AND product_id = %s',
            (quantity, session['user_id'], product_id)
        )

    conn.commit()

    # --- 计算返回值 ---

    # 当前商品的小计与数量
    cursor.execute('''
        SELECT p.price * c.quantity AS subtotal, c.quantity
        FROM carts c JOIN products p ON c.product_id = p.id
        WHERE c.user_id = %s AND c.product_id = %s
    ''', (session['user_id'], product_id))
    item = cursor.fetchone()
    subtotal = round(float(item['subtotal']), 2) if item else 0
    qty = item['quantity'] if item else 0

    # 购物车总价
    cursor.execute('''
        SELECT COALESCE(SUM(p.price * c.quantity), 0) AS total
        FROM carts c JOIN products p ON c.product_id = p.id
        WHERE c.user_id = %s
    ''', (session['user_id'],))
    total = round(float(cursor.fetchone()['total']), 2)

    # 购物车总数量（角标）
    cursor.execute(
        'SELECT COALESCE(SUM(quantity), 0) AS count FROM carts WHERE user_id = %s',
        (session['user_id'],)
    )
    cart_count = cursor.fetchone()['count']

    cursor.close()
    conn.close()

    return jsonify({
        'success': True,
        'subtotal': subtotal,
        'quantity': qty,
        'total': total,
        'cart_count': cart_count
    })


@app.route('/api/cart/delete/<int:product_id>', methods=['DELETE'])
@login_required
def api_cart_delete(product_id):
    """
    【Ajax】从购物车中删除指定商品。

    路径参数: product_id — 要删除的商品ID
    响应 JSON: { success, total, cart_count }
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM carts WHERE user_id = %s AND product_id = %s',
        (session['user_id'], product_id)
    )
    conn.commit()

    # 计算新的购物车总价
    cursor.execute('''
        SELECT COALESCE(SUM(p.price * c.quantity), 0) AS total
        FROM carts c JOIN products p ON c.product_id = p.id
        WHERE c.user_id = %s
    ''', (session['user_id'],))
    total = round(float(cursor.fetchone()['total']), 2)

    # 购物车总数量
    cursor.execute(
        'SELECT COALESCE(SUM(quantity), 0) AS count FROM carts WHERE user_id = %s',
        (session['user_id'],)
    )
    cart_count = cursor.fetchone()['count']

    cursor.close()
    conn.close()

    return jsonify({'success': True, 'total': total, 'cart_count': cart_count})


@app.route('/api/cart/count')
def api_cart_count():
    """
    【Ajax】获取当前用户购物车商品总数量。
    用于页面加载时更新导航栏角标。

    未登录时返回 count: 0
    """
    if 'user_id' not in session:
        return jsonify({'count': 0})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT COALESCE(SUM(quantity), 0) AS count FROM carts WHERE user_id = %s',
        (session['user_id'],)
    )
    count = cursor.fetchone()['count']
    cursor.close()
    conn.close()
    return jsonify({'count': count})


# ==================== 应用启动入口 ====================

# 在模块加载时初始化数据库。
# 放在 if __name__ 之外，是为了让 gunicorn 等 WSGI 服务器导入 app 对象时
# 也能自动建表 —— 生产环境不会执行 __main__ 分支，写在里面就不会生效。
try:
    init_db()
except Exception as e:
    print('[错误] 数据库初始化失败: {}'.format(e))
    print('请确认 db.py 中的数据库文件路径可写')


if __name__ == '__main__':
    # 本地开发入口。生产环境由 gunicorn 启动，不会走到这里。
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG') == '1'

    print('=' * 50)
    print('  臻品汇数码奢品商城启动中...')
    print('  默认管理员: admin / admin123')
    print('  http://localhost:{}'.format(port))
    print('=' * 50)

    app.run(debug=debug, host='0.0.0.0', port=port)
