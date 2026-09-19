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
    # is_active 用于「软删除」：0 表示已下架。
    # 为什么不用 DELETE：order_items.product_id 是指向 products 的外键，
    # 商品一旦被下过单就无法物理删除（会触发 FOREIGN KEY constraint failed）。
    # 改成软删除后，下架不再影响历史订单，也不会 500。
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            price       REAL    NOT NULL,
            stock       INTEGER NOT NULL DEFAULT 0,
            image_url   TEXT    DEFAULT '',
            description TEXT,
            category    TEXT    NOT NULL DEFAULT 'lifestyle',
            is_active   INTEGER NOT NULL DEFAULT 1,
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

    # --- 创建 stock_logs 库存流水表 ---
    # 记录每一次库存变动，用于回答「库存为什么变了」。
    # change_amount 正数=补货入库，负数=售出出库。
    # stock_after 存变动后的库存快照，方便直接看出当时的余量。
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id    INTEGER NOT NULL,
            product_name  TEXT    NOT NULL DEFAULT '',
            change_amount INTEGER NOT NULL,
            stock_after   INTEGER NOT NULL,
            reason        TEXT    NOT NULL DEFAULT 'manual',
            note          TEXT    DEFAULT '',
            operator      TEXT    DEFAULT '',
            created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_stock_logs_product ON stock_logs(product_id, created_at DESC)'
    )

    # --- 增量迁移：为老数据库补上后加的列 ---
    # create_tables() 只对新库生效，已经存在的 supermarket.db 不会因为
    # CREATE TABLE IF NOT EXISTS 而多出新列，所以这里显式补列。
    _ensure_column(cursor, 'products', 'is_active', 'INTEGER NOT NULL DEFAULT 1')

    # category 是新加的列。补列后已经是 'lifestyle' 的一律按名称重新归类，
    # 这样原来那 12 件种子商品的分组和改造前完全一致，不会全部掉进「生活」。
    if _ensure_column(cursor, 'products', 'category',
                      "TEXT NOT NULL DEFAULT 'lifestyle'"):
        cursor.execute('SELECT id, name FROM products')
        for row in cursor.fetchall():
            cursor.execute(
                'UPDATE products SET category = %s WHERE id = %s',
                (guess_category(row['name']), row['id'])
            )
        print('[迁移] 已为 products.category 回填分类')

    conn.commit()
    cursor.close()
    conn.close()


def _ensure_column(cursor, table, column, ddl):
    """
    幂等地给已有表补列（SQLite 没有 ADD COLUMN IF NOT EXISTS）。
    返回 True 表示这次确实新增了列，False 表示列本来就存在。
    调用方据此决定要不要做一次性的数据回填。
    """
    cursor.execute('PRAGMA table_info({})'.format(table))
    if column in {row['name'] for row in cursor.fetchall()}:
        return False
    cursor.execute('ALTER TABLE {} ADD COLUMN {} {}'.format(table, column, ddl))
    print('[迁移] 已为 {} 表新增 {}.{} 列'.format(table, table, column))
    return True


# 商品分类取值。key 存库，value 是界面文案。
CATEGORIES = {
    'digital': '数码',
    'luxury': '奢品',
    'lifestyle': '生活',
}

# 按名称猜分类的关键词表。
# 只有两处会用到：老数据的回填，以及管理员没选分类时的兜底。
# 这是启发式，不是权威 —— 新商品请让管理员在表单里显式选择分类。
_CATEGORY_KEYWORDS = {
    'digital': ('iPhone', 'MacBook', 'AirPods', 'Sony', 'Watch', 'Switch',
                '手机', '电脑', '耳机', '相机', '平板', '手表'),
    'luxury': ('LV', 'Dior', 'Herm', 'Gucci', 'Chanel', 'Neverfull',
               '爱马仕', '香奈儿', '包', '香水', '腰带', '手袋'),
}


def guess_category(name):
    """按商品名猜一个分类，猜不出就归入 lifestyle。"""
    text = name or ''
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(k in text for k in keywords):
            return category
    return 'lifestyle'


def log_stock_change(cursor, product_id, product_name, change_amount,
                     stock_after, reason, note='', operator=''):
    """
    写入一条库存流水。调用方负责 commit（通常和库存变更处于同一事务）。

    reason 取值：
        restock      管理员补货
        order        用户下单扣减
        order_cancel 订单取消回滚
    """
    cursor.execute(
        '''INSERT INTO stock_logs
           (product_id, product_name, change_amount, stock_after, reason, note, operator)
           VALUES (%s, %s, %s, %s, %s, %s, %s)''',
        (product_id, product_name, change_amount, stock_after, reason, note, operator)
    )


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
             '/static/products/iphone-16-pro-max.jpg',
             'Apple iPhone 16 Pro Max，256GB存储，钛金属原色，A18 Pro芯片，超视网膜XDR显示屏'),
            ('MacBook Air M4 13英寸', 8999.00, 30,
             '/static/products/macbook-air-m4.jpg',
             'Apple MacBook Air M4芯片，13.6英寸Liquid Retina显示屏，16GB内存，256GB SSD，午夜色'),
            ('AirPods Pro 3', 1899.00, 80,
             '/static/products/airpods-pro-3.jpg',
             'Apple AirPods Pro 第三代，主动降噪，自适应透明模式，个性化空间音频，MagSafe充电盒'),
            ('Sony WH-1000XM6 头戴耳机', 2499.00, 40,
             '/static/products/sony-wh1000xm6.jpg',
             '索尼旗舰级无线降噪头戴耳机，30小时续航，Hi-Res Audio认证，铂金银配色'),
            ('LV Neverfull MM 经典手袋', 12500.00, 15,
             '/static/products/lv-neverfull.jpg',
             'Louis Vuitton Neverfull MM，Monogram帆布，经典老花图案，托特包款'),
            ('Dior 真我女士香水 100ml', 1580.00, 60,
             '/static/products/dior-jadore.jpg',
             'Dior J\'adore 真我女士淡香精，花香调，依兰与大马士革玫瑰交织，经典优雅'),
            ('Hermès 经典H腰带', 6800.00, 25,
             '/static/products/hermes-belt.jpg',
             'Hermès 爱马仕 Collier de Chien 经典H扣腰带，Box小牛皮，镀钯金属件'),
            ('Apple Watch Ultra 3', 5999.00, 35,
             '/static/products/apple-watch-ultra-3.jpg',
             'Apple Watch Ultra 3，49mm钛金属表壳，精准双频GPS，2000尼特亮度'),
            ('Nintendo Switch OLED', 2599.00, 45,
             '/static/products/nintendo-switch-oled.jpg',
             '任天堂 Switch OLED款，7英寸OLED屏幕，64GB存储，Joy-Con手柄，白色'),
            ('Dyson V16 Detect 吸尘器', 4999.00, 30,
             '/static/products/dyson-v16.jpg',
             '戴森 V16 Detect 无绳手持吸尘器，激光探测微尘，LCD屏显，60分钟续航'),
            ('Gucci GG Marmont 链条包', 9800.00, 20,
             '/static/products/gucci-gg-marmont.jpg',
             'Gucci GG Marmont系列，绗缝V形皮革，双G金属logo，链条肩带，小号斜挎包'),
            ('Chanel N°5 经典香水', 1680.00, 55,
             '/static/products/chanel-no5.jpg',
             'Chanel 香奈儿 N°5 五号之水，醛香花香调，玛丽莲·梦露之选，100ml经典款'),
        ]
        # 分类由名称推导，避免在下面的商品清单里再手写一遍容易写错的分类字段
        cursor.executemany(
            'INSERT INTO products (name, price, stock, image_url, description, category) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            [row + (guess_category(row[0]),) for row in sample_products]
        )
        conn.commit()
        print('[初始化] 已插入 12 件示例商品')

    cursor.close()
    conn.close()
