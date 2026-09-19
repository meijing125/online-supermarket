# -*- coding: utf-8 -*-
"""
一次性迁移脚本：把已有商品记录的 image_url 从 placehold.co 占位图
替换为本地 /static/products/ 下的真实商品图。

背景：db.py 的种子数据只在 products 表为空时插入，
线上数据库已存在数据，因此必须直接 UPDATE，重启不会生效。
"""
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'supermarket.db')

MAP = {
    'iPhone 16 Pro Max 256GB':   'iphone-16-pro-max.jpg',
    'MacBook Air M4 13英寸':      'macbook-air-m4.jpg',
    'AirPods Pro 3':             'airpods-pro-3.jpg',
    'Sony WH-1000XM6 头戴耳机':   'sony-wh1000xm6.jpg',
    'LV Neverfull MM 经典手袋':   'lv-neverfull.jpg',
    'Dior 真我女士香水 100ml':    'dior-jadore.jpg',
    'Hermès 经典H腰带':           'hermes-belt.jpg',
    'Apple Watch Ultra 3':       'apple-watch-ultra-3.jpg',
    'Nintendo Switch OLED':      'nintendo-switch-oled.jpg',
    'Dyson V16 Detect 吸尘器':    'dyson-v16.jpg',
    'Gucci GG Marmont 链条包':    'gucci-gg-marmont.jpg',
    'Chanel N°5 经典香水':        'chanel-no5.jpg',
}

conn = sqlite3.connect(DB)
cur = conn.cursor()

total = 0
for name, img in MAP.items():
    cur.execute('UPDATE products SET image_url = ? WHERE name = ?',
                ('/static/products/' + img, name))
    total += cur.rowcount
conn.commit()

print('更新行数:', total)
print('--- 当前商品图片 ---')
for pid, name, url in cur.execute('SELECT id, name, image_url FROM products ORDER BY id'):
    print('  %2d  %-28s %s' % (pid, name, url))

conn.close()
