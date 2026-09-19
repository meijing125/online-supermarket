-- ============================================================================
-- 臻品汇数码奢品商城 —— 参考 DDL（MySQL 语法）
--
-- ⚠️ 这个文件**不参与程序运行**。
--    应用实际用的是 SQLite，建表逻辑在 db.py 的 create_tables() 里，
--    那才是唯一的权威定义。本文件只是为了课程报告/答辩时能一眼看清表结构，
--    以及万一要迁回 MySQL 时有个起点。
--
--    两边字段保持一致（含 is_active、category、stock_logs 与 5 种订单状态）。
--    改 db.py 的表结构时请顺手同步这里，否则又会变成一份骗人的文档。
--
-- 历史提醒：旧版这个文件里的 INSERT 语句，description 字段没加引号，
--    直接 `mysql < init.sql` 会语法报错；那批种子数据现在由 db.py 的
--    seed_products() 负责，所以这里只放结构，不放数据。
-- ============================================================================

-- CREATE DATABASE IF NOT EXISTS supermarket
--   DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- USE supermarket;

-- ---------------------------------------------------------------------------
-- 用户表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,          -- "salt_hex:key_hex"（PBKDF2-SHA256）
    is_admin      TINYINT(1)   NOT NULL DEFAULT 0,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 商品表
--   category  数码 digital / 奢品 luxury / 生活 lifestyle
--   is_active 软删除标记：0 = 已下架。不用 DELETE 是因为 order_items.product_id
--             是指向本表的外键，商品一旦被下过单就删不掉（外键约束报错）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(200)   NOT NULL,
    price       DECIMAL(10, 2) NOT NULL,
    stock       INT            NOT NULL DEFAULT 0,
    image_url   VARCHAR(500)   DEFAULT '',
    description TEXT,
    category    VARCHAR(20)    NOT NULL DEFAULT 'lifestyle',
    is_active   TINYINT(1)     NOT NULL DEFAULT 1,
    created_at  TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_products_active_category (is_active, category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 购物车表（同一用户对同一商品只保留一条记录）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS carts (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    product_id INT NOT NULL,
    quantity   INT NOT NULL DEFAULT 1,
    UNIQUE KEY uk_cart_user_product (user_id, product_id),
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 订单表
--   状态流转：pending → paid → shipped → completed
--             未完成状态可 → cancelled（取消时商品数量加回库存）
--   目前 checkout 下单直接写入 paid。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT            NOT NULL,
    total_amount   DECIMAL(10, 2) NOT NULL,
    status         VARCHAR(20)    DEFAULT 'pending',
    address        VARCHAR(500)   DEFAULT '',
    payment_method VARCHAR(20)    DEFAULT '',
    created_at     TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    INDEX idx_orders_user (user_id, created_at),
    INDEX idx_orders_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 订单明细表
--   product_name / price 是下单当时的快照：商品之后改名或调价，
--   历史订单显示的仍是下单时的信息。
--   product_id 不设 ON DELETE，配合 products.is_active 软删除使用。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_items (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    order_id     INT            NOT NULL,
    product_id   INT            NOT NULL,
    product_name VARCHAR(200)   NOT NULL,
    price        DECIMAL(10, 2) NOT NULL,
    quantity     INT            NOT NULL,
    FOREIGN KEY (order_id)   REFERENCES orders(id)   ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id),
    INDEX idx_order_items_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 库存流水表
--   每次库存变动都写一条，回答「库存为什么变成这样」。
--   change_amount 正数=入库（补货/取消回滚），负数=出库（售出）。
--   stock_after 是变动后的余量快照，方便直接对账。
--   reason: restock 补货 / order 售出 / order_cancel 取消回滚 / manual 手工编辑
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_logs (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    product_id    INT          NOT NULL,
    product_name  VARCHAR(200) NOT NULL DEFAULT '',
    change_amount INT          NOT NULL,
    stock_after   INT          NOT NULL,
    reason        VARCHAR(20)  NOT NULL DEFAULT 'manual',
    note          VARCHAR(255) DEFAULT '',
    operator      VARCHAR(50)  DEFAULT '',
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id),
    INDEX idx_stock_logs_product (product_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
