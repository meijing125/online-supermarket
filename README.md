# 臻品汇数码奢品商城

一个基于 **Python Flask + SQLite** 的网上超市 / 商城系统，前端使用原生 JavaScript + Bootstrap 5。

## 功能模块

| 模块 | 说明 |
| --- | --- |
| 用户系统 | 注册 / 登录 / 退出，密码使用 PBKDF2-SHA256（10万次迭代 + 随机盐）哈希存储 |
| 商品展示 | 首页商品列表，按上架时间倒序；售罄商品自动禁购 |
| 商品分类 | `products.category` 分 数码 / 奢品 / 生活，管理员上架时选择，首页按分类筛选 |
| 搜索与分页 | 首页支持按名称/描述搜索，每页 12 件；筛选、搜索、分页可叠加使用 |
| 后台管理 | 管理员上架新品、编辑商品、上下架（软删除，可重新上架）、上传商品图片 |
| 快捷补货 | 在现有库存上追加数量（`stock = stock + N`），不覆盖商品其它字段 |
| 库存流水 | `stock_logs` 表记录每次库存变动的数量、变动后余量、原因、操作人与时间 |
| 购物车 | Ajax 异步增删改，实时更新小计、总价与导航栏角标 |
| 结算下单 | 条件更新扣减库存（防超卖）→ 创建订单 → 清空购物车，全程事务保护 |
| 订单记录 | 用户查看自己的历史订单、单笔订单详情，并可自助取消未发货的订单 |
| 订单管理 | 管理员查看**全部用户**订单，按状态筛选、搜索、分页，查看明细并流转状态 |

### 商品分类

| key | 名称 |
| --- | --- |
| `digital` | 数码 |
| `luxury` | 奢品 |
| `lifestyle` | 生活 |

管理员在上架表单里显式选择分类；若没选，`db.guess_category()` 会按商品名猜一个兜底。

### 订单状态

```
已支付 paid ──► 已发货 shipped ──► 已完成 completed
   │                │
   └────────────────┴──────────► 已取消 cancelled   （取消时商品数量自动加回库存）
```

### 库存为什么会变

每次库存变动都会写一条流水（`stock_logs`），原因取值：

| reason | 含义 |
| --- | --- |
| `restock` | 管理员补货（+N） |
| `order` | 用户下单售出（−N） |
| `order_cancel` | 订单取消，库存回滚（+N） |
| `manual` | 管理员在编辑弹窗里直接改库存 |

在「后台管理 → 商品管理 → 补货」弹窗里可以直接看到该商品的流水。

### 商品为什么是「下架」而不是「删除」

`order_items.product_id` 是指向 `products` 的外键且没有 `ON DELETE` 规则，
商品只要被下过一次单，`DELETE` 就会触发外键约束而失败。所以下架走的是
`products.is_active = 0` 软删除：商品从首页消失、不可加购，但历史订单
（依赖商品快照）完好无损，也能随时重新上架。

## 技术栈

- **后端**：Flask 3.x
- **数据库**：SQLite（通过 `db.py` 适配层封装）
- **前端**：原生 JavaScript + Bootstrap 5 + Jinja2 模板
- **部署**：Gunicorn + Render

## 项目结构

```
online-supermarket/
├── app.py                  # 主程序：路由、业务逻辑、鉴权
├── db.py                   # 数据库访问层（SQLite 适配 + 建表 + 初始数据）
├── requirements.txt        # 依赖清单
├── render.yaml             # Render 部署蓝图（备选方案）
├── .env.example            # 环境变量示例
├── init.sql                # 早期 MySQL 版建表脚本（已废弃，保留备查）
├── deploy/
│   └── pythonanywhere_wsgi.py   # PythonAnywhere 的 WSGI 入口配置模板
├── static/
│   ├── style.css
│   └── uploads/            # 用户上传的商品图片
└── templates/              # Jinja2 页面模板
```

## 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（首次运行会自动建表并插入 12 件示例商品）
python app.py

# 3. 浏览器打开 http://localhost:5000
```

数据库文件 `supermarket.db` 会在首次启动时自动生成在项目根目录，无需任何额外配置。

**默认管理员账号：`admin` / `admin123`**

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SECRET_KEY` | Flask Session 加密密钥。不设置则每次启动随机生成，重启后所有用户会被登出 | 随机值 |
| `DB_PATH` | SQLite 数据库文件路径 | 项目根目录 `supermarket.db` |
| `PORT` | 监听端口 | `5000` |
| `FLASK_DEBUG` | 设为 `1` 开启调试模式（**生产环境不要开启**） | 关闭 |

生成一个固定密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 部署到线上

### 方案一：PythonAnywhere（推荐）

免费、无需信用卡，**且家目录是持久化的 —— SQLite 数据文件不会随重启丢失**，
用户、订单、库存都能长期保留。网址形如 `https://你的用户名.pythonanywhere.com`。

1. 注册免费账号：<https://www.pythonanywhere.com/pricing/> → **Create a Beginner account**
2. 打开 **Consoles** → **Bash**，执行：

   ```bash
   git clone https://github.com/你的用户名/online-supermarket.git
   pip install --user flask
   ```

3. 打开 **Web** → **Add a new web app** → **Manual configuration** → 选 Python 3.10+
4. 在 Web 页面把 **Source code** 填成 `/home/你的用户名/online-supermarket`，
   再点 **WSGI configuration file**，把内容整体替换为
   `deploy/pythonanywhere_wsgi.py` 的内容，并按其注释修改两处（项目路径、SECRET_KEY）
5. 点 **Reload**，访问 `https://你的用户名.pythonanywhere.com`

> 免费账号每 3 个月需要在 Web 页面点一次续期按钮，否则应用会被暂停。

### 方案二：Render

> 注意：`render.com` 在中国大陆多数网络下无法访问，若你身处大陆请优先用方案一。

1. 打开 [render.com](https://render.com) → **New** → **Blueprint** → 选择本仓库。
2. Render 会自动读取仓库根目录的 `render.yaml`，点击确认即可。
   （或选 **Web Service** 手动配置：Build Command 填 `pip install -r requirements.txt`，
   Start Command 填 `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT app:app`。）
3. 部署完成后会得到一个 `https://xxx.onrender.com` 的公开网址。

## 已知限制

这几点是 SQLite + 免费云平台的固有限制，部署前请知悉：

1. **免费实例会休眠**：Render 免费套餐在 15 分钟无访问后会休眠，下次访问需要约 30 秒冷启动。
2. **上传的图片会在重启后丢失**：免费套餐没有持久化磁盘，`static/uploads/` 下的图片在实例
   重启或重新部署后会消失。示例商品使用的是外部图床链接，不受影响。
3. **数据库会被重置**：同上，`supermarket.db` 也没有持久化。若需保留数据，可在 Render 上
   挂载付费磁盘并把 `DB_PATH` 指向该磁盘。
4. **演示用并发模型**：SQLite 采用数据库级写锁，无法像 MySQL 那样用 `SELECT ... FOR UPDATE`
   做行级锁。当前实现为单 worker + 多线程，能应对演示规模，但不适合高并发抢购场景。
5. **默认管理员密码**：`admin123` 仅用于演示，公开部署后请及时修改。

## 测试

- `商城_测试用例.md` / `商城_测试用例.xlsx` — 功能测试用例
- `gen_xlsx.py` — 测试用例表格生成脚本
