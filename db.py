# -*- coding: utf-8 -*-
"""
数据库访问层 —— SQLite

为什么用 SQLite：
    项目原先使用 MySQL(PyMySQL)，但云平台上的 MySQL 实例通常不免费。
    SQLite 把整个数据库存成单个文件，零配置、零费用，适合课程作业与演示部署。

为什么要有这层适配：
    app.py 里有 20 多处业务 SQL 是按 PyMySQL 的习惯写的（%s 占位符、DictCursor 字典结果）。
    为了不改动这些业务代码、降低改错风险，这里做了一层薄适配：
        1. 自动把 %s 占位符翻译成 SQLite 的 ?
        2. 查询结果自动返回 dict（等价于 PyMySQL 的 DictCursor）
        3. 自动剔除 SQLite 不支持的 FOR UPDATE
    这样 app.py 的路由代码一行都不用改。
"""

import datetime
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据库文件路径。部署到云平台时可用环境变量 DB_PATH 指向挂载的持久化磁盘
DB_PATH = os.environ.get('DB_PATH') or os.path.join(BASE_DIR, 'supermarket.db')


def _convert_timestamp(value):
    """
    把 SQLite 中 TIMESTAMP 列的文本值转成 datetime 对象。

    为什么需要这个：
        MySQL 驱动会把 TIMESTAMP 列直接返回成 datetime 对象，而 SQLite 没有原生
        日期类型，只能存字符串。模板里写的是 p.created_at.strftime('%Y-%m-%d')，
        若不转换就会抛 "'str' object has no attribute 'strftime'"。
        注册这个转换器后，SQLite 的返回类型与 MySQL 保持一致。
    """
    text = value.decode('utf-8') if isinstance(value, (bytes, bytearray)) else value
    try:
        # Python 3.11+ 能直接解析 'YYYY-MM-DD HH:MM:SS'
        return datetime.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        pass
    # 兼容更早的 Python 版本以及带毫秒的写法
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(text, fmt)
        except (ValueError, TypeError):
            continue
    return value


# 按建表时声明的列类型做转换（配合下面的 PARSE_DECLTYPES 生效）
sqlite3.register_converter('TIMESTAMP', _convert_timestamp)
sqlite3.register_converter('DATETIME', _convert_timestamp)


class Cursor:
    """
    包装 sqlite3.Cursor，使其接口与 PyMySQL 的 DictCursor 保持一致。

    业务代码里用到的接口：execute / executemany / fetchone / fetchall /
    lastrowid / close。查询结果统一是 dict，所以 row['price'] 这种写法照常可用。
    """

    def __init__(self, raw):
        self._raw = raw

    @staticmethod
    def _translate(sql):
        """把 PyMySQL 风格的 SQL 翻译成 SQLite 能执行的 SQL。"""
        # 1. %s 占位符 → ?
        sql = sql.replace('%s', '?')
        # 2. SQLite 没有行级锁语法，去掉 FOR UPDATE
        sql = sql.replace(' FOR UPDATE', '')
        return sql

    def execute(self, sql, params=()):
        self._raw.execute(self._translate(sql), params)
        return self

    def executemany(self, sql, seq_of_params):
        self._raw.executemany(self._translate(sql), seq_of_params)
        return self

    def fetchone(self):
        row = self._raw.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(row) for row in self._raw.fetchall()]

    @property
    def lastrowid(self):
        """INSERT 之后获取自增主键，与 PyMySQL 用法一致。"""
        return self._raw.lastrowid

    @property
    def rowcount(self):
        return self._raw.rowcount

    def close(self):
        self._raw.close()


class Connection:
    """包装 sqlite3.Connection，提供 PyMySQL 风格的连接级接口。"""

    def __init__(self, raw):
        self._raw = raw

    def cursor(self):
        return Cursor(self._raw.cursor())

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def get_db():
    """
    获取一个新的数据库连接。

    调用方式与原 PyMySQL 版本完全一致：
        conn = get_db()
        cursor = conn.cursor()
        ...
        cursor.close()
        conn.close()
    """
    # timeout 让并发写入时先等待而不是立刻抛 "database is locked"
    # PARSE_DECLTYPES 让 TIMESTAMP 列按上面注册的转换器返回 datetime 而非字符串
    conn = sqlite3.connect(DB_PATH, timeout=10, detect_types=sqlite3.PARSE_DECLTYPES)
    # 让查询结果可以按列名访问（row['price']），即 PyMySQL 的 DictCursor 效果
    conn.row_factory = sqlite3.Row
    # 关键：SQLite 默认不启用外键约束，必须每个连接显式打开，
    # 否则 carts / order_items 上的 ON DELETE CASCADE 不会生效
    conn.execute('PRAGMA foreign_keys = ON')
    return Connection(conn)


# ==================== 数据库初始化 ====================

def create_tables():
    """
    创建所有必需的表（如果尚不存在）。
    幂等 —— 重复调用不会有副作用。
    """
    conn = get_db()
    cursor = conn.cursor()

    # --- 创建 users 用户表 ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- 创建 products 商品表 ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            price       REAL    NOT NULL,
            stock       INTEGER NOT NULL DEFAULT 0,
            image_url   TEXT    DEFAULT '',
            description TEXT,
            created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- 创建 carts 购物车表 ---
    # UNIQUE 约束确保同一用户对同一商品只有一条购物车记录
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS carts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity   INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            UNIQUE (user_id, product_id)
        )
    ''')

    # --- 创建 orders 订单表 ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            total_amount   REAL    NOT NULL,
            status         TEXT    DEFAULT 'pending',
            address        TEXT    DEFAULT '',
            payment_method TEXT    DEFAULT '',
            created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # --- 创建 order_items 订单明细表 ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id     INTEGER NOT NULL,
            product_id   INTEGER NOT NULL,
            product_name TEXT    NOT NULL,
            price        REAL    NOT NULL,
            quantity     INTEGER NOT NULL,
            FOREIGN KEY (order_id)   REFERENCES orders(id)   ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')

    conn.commit()
    cursor.close()
    conn.close()


def seed_products():
    """
    插入示例商品数据（仅当商品表为空时），避免每次启动重复插入。
    与管理员的创建分开：管理员账户需要用到 app.py 里的密码哈希函数，
    放在 app.py 的 init_db() 中完成，以免 db.py 反向依赖 app.py 造成循环导入。
    """
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) AS cnt FROM products')
    if cursor.fetchone()['cnt'] == 0:
        sample_products = [
            ('iPhone 16 Pro Max 256GB', 9999.00, 50,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=iPhone+16+Pro+Max',
             'Apple iPhone 16 Pro Max，256GB存储，钛金属原色，A18 Pro芯片，超视网膜XDR显示屏'),
            ('MacBook Air M4 13英寸', 8999.00, 30,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=MacBook+Air+M4',
             'Apple MacBook Air M4芯片，13.6英寸Liquid Retina显示屏，16GB内存，256GB SSD，午夜色'),
            ('AirPods Pro 3', 1899.00, 80,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=AirPods%20Pro%203',
             'Apple AirPods Pro 第三代，主动降噪，自适应透明模式，个性化空间音频，MagSafe充电盒'),
            ('Sony WH-1000XM6 头戴耳机', 2499.00, 40,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Sony%20WH-1000XM6%20%E5%A4%B4%E6%88%B4%E8%80%B3%E6%9C%BA',
             '索尼旗舰级无线降噪头戴耳机，30小时续航，Hi-Res Audio认证，铂金银配色'),
            ('LV Neverfull MM 经典手袋', 12500.00, 15,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=LV%20Neverfull%20MM%20%E7%BB%8F%E5%85%B8%E6%89%8B%E8%A2%8B',
             'Louis Vuitton Neverfull MM，Monogram帆布，经典老花图案，托特包款'),
            ('Dior 真我女士香水 100ml', 1580.00, 60,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Dior%20%E7%9C%9F%E6%88%91%E5%A5%B3%E5%A3%AB%E9%A6%99%E6%B0%B4%20100ml',
             'Dior J\'adore 真我女士淡香精，花香调，依兰与大马士革玫瑰交织，经典优雅'),
            ('Hermès 经典H腰带', 6800.00, 25,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Herm%C3%A8s%20%E7%BB%8F%E5%85%B8H%E8%85%B0%E5%B8%A6',
             'Hermès 爱马仕 Collier de Chien 经典H扣腰带，Box小牛皮，镀钯金属件'),
            ('Apple Watch Ultra 3', 5999.00, 35,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Apple%20Watch%20Ultra%203',
             'Apple Watch Ultra 3，49mm钛金属表壳，精准双频GPS，2000尼特亮度'),
            ('Nintendo Switch OLED', 2599.00, 45,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Nintendo%20Switch%20OLED',
             '任天堂 Switch OLED款，7英寸OLED屏幕，64GB存储，Joy-Con手柄，白色'),
            ('Dyson V16 Detect 吸尘器', 4999.00, 30,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Dyson%20V16%20Detect%20%E5%90%B8%E5%B0%98%E5%99%A8',
             '戴森 V16 Detect 无绳手持吸尘器，激光探测微尘，LCD屏显，60分钟续航'),
            ('Gucci GG Marmont 链条包', 9800.00, 20,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Gucci%20GG%20Marmont%20%E9%93%BE%E6%9D%A1%E5%8C%85',
             'Gucci GG Marmont系列，绗缝V形皮革，双G金属logo，链条肩带，小号斜挎包'),
            ('Chanel N°5 经典香水', 1680.00, 55,
             'https://placehold.co/400x300/1d1d1f/c9a04c?text=Chanel%20N%C2%B05%20%E7%BB%8F%E5%85%B8%E9%A6%99%E6%B0%B4',
             'Chanel 香奈儿 N°5 五号之水，醛香花香调，玛丽莲·梦露之选，100ml经典款'),
        ]
        cursor.executemany(
            'INSERT INTO products (name, price, stock, image_url, description) VALUES (%s, %s, %s, %s, %s)',
            sample_products
        )
        conn.commit()
        print('[初始化] 已插入 12 件示例商品')

    cursor.close()
    conn.close()
