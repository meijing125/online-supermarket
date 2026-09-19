-- ============================================
-- 网上超市商城系统 - 数据库初始化脚本
-- 使用方法：mysql -u root -p < init.sql
-- ============================================

-- 创建数据库（使用UTF8MB4字符集以支持中文和Emoji）
CREATE DATABASE IF NOT EXISTS supermarket
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE supermarket;

-- ============================================
-- 1. 用户表 (users)
-- 密码使用PBKDF2-SHA256哈希加密存储（在应用层完成）
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '用户ID',
    username VARCHAR(50) NOT NULL UNIQUE COMMENT '用户名',
    password_hash VARCHAR(255) NOT NULL COMMENT 'PBKDF2-SHA256加密后的密码',
    is_admin TINYINT(1) DEFAULT 0 COMMENT '是否管理员：0=否, 1=是',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- ============================================
-- 2. 商品表 (products)
-- ============================================
CREATE TABLE IF NOT EXISTS products (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '商品ID',
    name VARCHAR(200) NOT NULL COMMENT '商品名称',
    price DECIMAL(10,2) NOT NULL COMMENT '商品价格（元）',
    stock INT NOT NULL DEFAULT 0 COMMENT '库存数量',
    image_url VARCHAR(500) DEFAULT '' COMMENT '商品图片URL',
    description TEXT COMMENT '商品描述',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上架时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品表';

-- ============================================
-- 3. 购物车表 (carts)
-- 同一用户对同一商品只能有一条记录，通过UNIQUE约束保证
-- ============================================
CREATE TABLE IF NOT EXISTS carts (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '购物车记录ID',
    user_id INT NOT NULL COMMENT '用户ID',
    product_id INT NOT NULL COMMENT '商品ID',
    quantity INT NOT NULL DEFAULT 1 COMMENT '数量',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE KEY uk_user_product (user_id, product_id) COMMENT '同一用户对同一商品唯一'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='购物车表';

-- ============================================
-- 4. 订单表 (orders)
-- ============================================
CREATE TABLE IF NOT EXISTS orders (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '订单ID',
    user_id INT NOT NULL COMMENT '用户ID',
    total_amount DECIMAL(10,2) NOT NULL COMMENT '订单总金额',
    status VARCHAR(20) DEFAULT 'pending' COMMENT '订单状态: pending=待支付, paid=已支付',
    address VARCHAR(500) DEFAULT '' COMMENT '收货地址',
    payment_method VARCHAR(50) DEFAULT '' COMMENT '支付方式: wechat=微信, alipay=支付宝',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '下单时间',
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单表';

-- ============================================
-- 5. 订单明细表 (order_items)
-- 记录每笔订单中每个商品的购买详情
-- ============================================
CREATE TABLE IF NOT EXISTS order_items (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '明细ID',
    order_id INT NOT NULL COMMENT '订单ID',
    product_id INT NOT NULL COMMENT '商品ID',
    product_name VARCHAR(200) NOT NULL COMMENT '商品名称（快照）',
    price DECIMAL(10,2) NOT NULL COMMENT '购买时单价',
    quantity INT NOT NULL COMMENT '购买数量',
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细表';

-- ============================================
-- 示例商品数据
-- 注：管理员账户由应用首次启动时自动创建
--     默认账号: admin / 密码: admin123
-- ============================================
INSERT INTO products (name, price, stock, image_url, description) VALUES
('iPhone 16 Pro Max 256GB', 9999.00, 50, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=iPhone%2016%20Pro%20Max%20256GB', Apple iPhone 16 Pro Max，256GB存储，钛金属原色，A18 Pro芯片,
('MacBook Air M4 13英寸', 8999.00, 30, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=MacBook%20Air%20M4%2013%E8%8B%B1%E5%AF%B8', Apple MacBook Air M4芯片，13.6英寸，16GB/256GB，午夜色,
('AirPods Pro 3', 1899.00, 80, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=AirPods%20Pro%203', Apple AirPods Pro 第三代，主动降噪，MagSafe充电盒,
('Sony WH-1000XM6 头戴耳机', 2499.00, 40, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=Sony%20WH-1000XM6%20%E5%A4%B4%E6%88%B4%E8%80%B3%E6%9C%BA', 索尼旗舰无线降噪头戴耳机，30小时续航，Hi-Res认证,
('LV Neverfull MM 经典手袋', 12500.00, 15, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=LV%20Neverfull%20MM%20%E7%BB%8F%E5%85%B8%E6%89%8B%E8%A2%8B', Louis Vuitton Neverfull MM，Monogram帆布托特包,
('Dior 真我女士香水 100ml', 1580.00, 60, 'https://images.unsplash.com/photo-1541643600914-78b084683601?w=400&h=300&fit=crop', 'Dior J''adore 真我女士淡香精，花香调，100ml'),
('Hermès 经典H腰带', 6800.00, 25, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=Herm%C3%A8s%20%E7%BB%8F%E5%85%B8H%E8%85%B0%E5%B8%A6', Hermès 爱马仕 Collier de Chien H扣腰带，Box小牛皮,
('Apple Watch Ultra 3', 5999.00, 35, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=Apple%20Watch%20Ultra%203', Apple Watch Ultra 3，49mm钛金属，双频GPS,
('Nintendo Switch OLED', 2599.00, 45, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=Nintendo%20Switch%20OLED', 任天堂 Switch OLED款，7英寸屏幕，64GB存储,
('Dyson V16 Detect 吸尘器', 4999.00, 30, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=Dyson%20V16%20Detect%20%E5%90%B8%E5%B0%98%E5%99%A8', 戴森 V16 Detect 无绳吸尘器，激光探测，60分钟续航,
('Gucci GG Marmont 链条包', 9800.00, 20, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=Gucci%20GG%20Marmont%20%E9%93%BE%E6%9D%A1%E5%8C%85', Gucci GG Marmont，绗缝皮革，双G金属logo，斜挎包,
('Chanel N°5 经典香水', 1680.00, 55, 'https://placehold.co/400x300/1d1d1f/c9a04c?text=Chanel%20N%C2%B05%20%E7%BB%8F%E5%85%B8%E9%A6%99%E6%B0%B4', Chanel N°5 五号之水，醛香花香调，100ml经典款;
