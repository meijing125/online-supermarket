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
  3. 后台管理 - 上新品 (仅管理员)
  4. 购物车系统 (Ajax 异步操作)
  5. 结账系统 (库存扣减 + 事务保护)
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from functools import wraps
from werkzeug.utils import secure_filename
import hashlib
import os
import uuid
from datetime import datetime

# 数据库访问层（SQLite 适配，详见 db.py）
from db import get_db, create_tables, seed_products

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
            # 判断是否为 AJAX 请求：检查请求头或路径前缀
            is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                       or request.path.startswith('/api/')
                       or request.headers.get('Accept') == 'application/json'
                       or request.is_json)
            if is_ajax:
                return jsonify({'success': False, 'message': '请先登录', 'redirect': url_for('login')}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """
    管理员权限验证装饰器。
    若用户非管理员（is_admin != True），返回 403 禁止访问。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            return '权限不足：仅管理员可访问此页面', 403
        return f(*args, **kwargs)
    return decorated


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


# ==================== 页面路由 ====================

@app.route('/')
def index():
    """
    首页 — 展示所有商品列表。
    从 products 表读取全部商品，按上架时间倒序排列。
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products ORDER BY id DESC')
    products = cursor.fetchall()
    # 将 Decimal 类型转为 float，便于模板中显示和 JSON 序列化
    for p in products:
        p['price'] = float(p['price'])
    cursor.close()
    conn.close()
    return render_template('index.html', products=products)


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
        return render_template('login.html')

    # --- 处理登录表单提交 ---
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    # 前端已有 required 验证，此处为后端兜底校验
    if not username or not password:
        return render_template('login.html', error='请输入用户名和密码')

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
            return render_template('login.html', error='用户名不存在')

        # 第二步：使用 PBKDF2 验证密码是否匹配
        if not verify_password(password, user['password_hash']):
            # 密码错误 → 给出明确错误提示（与"用户名不存在"相区分）
            return render_template('login.html', error='密码错误')

        # 登录成功：将用户关键信息存入 Session
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['is_admin'] = bool(user['is_admin'])

        return redirect(url_for('index'))

    except Exception as e:
        # 数据库连接失败、查询异常等意外错误 → 给出友好提示而非报错页面
        app.logger.error(f'登录异常: {e}')
        return render_template('login.html', error='登录服务暂时不可用，请稍后重试')
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
    """辅助函数：查询所有商品，转换 Decimal → float"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products ORDER BY id DESC')
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
        return render_template('admin.html', products=products)

    # --- 处理新品上架表单 ---
    name = request.form.get('name', '').strip()
    price_str = request.form.get('price', '').strip()
    stock_str = request.form.get('stock', '').strip()
    image_url = request.form.get('image_url', '').strip()
    description = request.form.get('description', '').strip()

    # --- 后端数据校验 ---
    errors = []
    if not name:
        errors.append('请输入商品名称')

    try:
        price = float(price_str)
        if price <= 0:
            errors.append('商品价格必须大于 0')
    except (ValueError, TypeError):
        errors.append('请输入有效的商品价格')

    try:
        stock = int(stock_str)
        if stock < 0:
            errors.append('库存数量不能为负数')
    except (ValueError, TypeError):
        errors.append('请输入有效的库存数量')

    if errors:
        products = _get_all_products()
        return render_template('admin.html', error='；'.join(errors), products=products)

    # 校验通过：将商品数据插入数据库
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO products (name, price, stock, image_url, description) VALUES (%s, %s, %s, %s, %s)',
            (name, price, stock, image_url if image_url else '', description)
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

    # 校验
    errors = []
    if not name:
        errors.append('请输入商品名称')
    try:
        price = float(price_str)
        if price <= 0:
            errors.append('商品价格必须大于 0')
    except (ValueError, TypeError):
        errors.append('请输入有效的商品价格')
    try:
        stock = int(stock_str)
        if stock < 0:
            errors.append('库存数量不能为负数')
    except (ValueError, TypeError):
        errors.append('请输入有效的库存数量')

    if errors:
        return jsonify({'success': False, 'message': '；'.join(errors)})

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE products SET name=%s, price=%s, stock=%s, image_url=%s, description=%s WHERE id=%s',
            (name, price, stock, image_url, description, product_id)
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
    【Ajax】下架（删除）商品。
    响应 JSON: { success: bool, message: str }
    """
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM products WHERE id = %s', (product_id,))
        conn.commit()
        return jsonify({'success': True, 'message': '商品已下架'})
    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f'删除商品异常: {e}')
        return jsonify({'success': False, 'message': '下架失败，请稍后重试'}), 500
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

    return render_template('orders.html', orders=order_list)


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
    cursor.execute('''
        SELECT
            c.id AS cart_id,
            c.quantity,
            p.id AS product_id,
            p.name,
            p.price,
            p.stock,
            p.image_url
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
            p.id AS product_id, p.name, p.price, p.stock, p.image_url
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
        # --- 第一步：逐项检查库存是否充足 ---
        # 使用 SELECT ... FOR UPDATE 行级锁，防止并发超卖
        for item in items:
            cursor.execute(
                'SELECT stock, name FROM products WHERE id = %s FOR UPDATE',
                (item['product_id'],)
            )
            product = cursor.fetchone()
            if product['stock'] < item['quantity']:
                # 库存不足 → 回滚事务，返回错误提示
                conn.rollback()
                cursor.close()
                conn.close()
                return render_template(
                    'checkout.html', items=items, total=total,
                    error='商品“{}”库存不足，当前仅剩 {} 件'.format(
                        product['name'], product['stock']
                    )
                )

        # --- 第二步：逐项扣减库存 ---
        for item in items:
            cursor.execute(
                'UPDATE products SET stock = stock - %s WHERE id = %s',
                (item['quantity'], item['product_id'])
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

        # 跳转到订单成功页
        return render_template('order_success.html', order_id=order_id, total=total)

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

    # 验证商品是否存在
    cursor.execute('SELECT id, stock FROM products WHERE id = %s', (product_id,))
    product = cursor.fetchone()
    if not product:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': '商品不存在'})

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
