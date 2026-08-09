"""演示数据种子脚本：为 Docker 测试库（PG + MySQL）重建 demo schema 与数据。

用法:
    python scripts/seed_demo.py            # 两个库都重建
    python scripts/seed_demo.py --skip-pg  # 只重建 MySQL

连接参数默认与 docker-compose.yml 一致，可用环境变量覆盖：
    DB_ASSISTANT_DEMO_PG_HOST / _PORT / _USER / _PASSWORD / _DB
    DB_ASSISTANT_DEMO_MYSQL_HOST / _PORT / _USER / _PASSWORD / _DB

包含结构（贴合 glossary 示例）：users / products（JSON 规格列）/ orders /
categories（自引用分类树，可跑 WITH RECURSIVE）/ v_order_stats（视图）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

PG_DEFAULTS = dict(host="127.0.0.1", port=54329, user="test", password="test", db="testdb")
MYSQL_DEFAULTS = dict(host="127.0.0.1", port=33069, user="root", password="test", db="testdb")

INSERT_USERS = """
INSERT INTO users (name, email, phone, status, created_at) VALUES
('张三', 'zhangsan@example.com', '13800000001', 'active',   '2026-01-05 10:00:00'),
('李四', 'lisi@example.com',     '13800000002', 'active',   '2026-01-12 09:30:00'),
('王五', 'wangwu@example.com',   '13800000003', 'inactive', '2026-02-01 14:00:00'),
('赵六', 'zhaoliu@example.com',  '13800000004', 'active',   '2026-02-20 16:20:00'),
('孙七', 'sunqi@example.com',    '13800000005', 'active',   '2026-03-03 11:45:00');
"""

INSERT_PRODUCTS = """
INSERT INTO products (name, price, stock, status) VALUES
('无线鼠标',   89.00, 120, 'on_sale'),
('机械键盘',   399.00, 45, 'on_sale'),
('4K 显示器',  1999.00, 8, 'on_sale'),
('USB-C 扩展坞', 259.00, 0, 'out_of_stock'),
('降噪耳机',   1299.00, 30, 'on_sale'),
('便携充电宝',  149.00, 200, 'on_sale');
"""

INSERT_ORDERS = """
INSERT INTO orders (user_id, product_id, quantity, total_amount, order_status, user_order_dt) VALUES
(1, 1, 2, 178.00,  'completed', '2026-03-05 10:12:00'),
(1, 5, 1, 1299.00, 'paid',      '2026-04-01 15:40:00'),
(2, 2, 1, 399.00,  'shipped',   '2026-04-10 09:05:00'),
(2, 6, 3, 447.00,  'completed', '2026-04-15 20:30:00'),
(3, 3, 1, 1999.00, 'cancelled', '2026-05-02 13:22:00'),
(4, 1, 1, 89.00,   'paid',      '2026-05-18 11:08:00'),
(4, 4, 2, 518.00,  'pending',   '2026-06-01 17:55:00'),
(5, 5, 1, 1299.00, 'shipped',   '2026-06-20 08:47:00'),
(5, 2, 2, 798.00,  'pending',   '2026-07-01 19:15:00'),
(1, 6, 5, 745.00,  'completed', '2026-07-15 12:00:00');
"""

INSERT_CATEGORIES = """
INSERT INTO categories (parent_id, name, sort_order) VALUES
(NULL, '电子产品', 1),
(1,   '电脑外设', 1),
(1,   '显示设备', 2),
(1,   '音频设备', 3),
(2,   '鼠标', 1),
(2,   '键盘', 2),
(2,   '扩展坞', 3),
(3,   '显示器', 1),
(4,   '耳机', 1),
(4,   '音箱', 2);
"""

PG_DDL = """
DROP TABLE IF EXISTS orders, categories, products, users CASCADE;
CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(200) NOT NULL,
  phone VARCHAR(20),
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE products (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  price NUMERIC(10,2) NOT NULL,
  stock INT NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'on_sale',
  specs JSONB
);
CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  product_id INT NOT NULL REFERENCES products(id),
  quantity INT NOT NULL,
  total_amount NUMERIC(10,2) NOT NULL,
  order_status VARCHAR(20) NOT NULL DEFAULT 'pending',
  user_order_dt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE categories (
  id SERIAL PRIMARY KEY,
  parent_id INT REFERENCES categories(id),
  name VARCHAR(100) NOT NULL,
  sort_order INT NOT NULL DEFAULT 0
);
CREATE OR REPLACE VIEW v_order_stats AS
SELECT u.id AS user_id, u.name AS user_name,
       count(o.id) AS order_count,
       round(sum(o.total_amount), 2) AS total_spent,
       round(avg(o.total_amount), 2) AS avg_order
FROM users u LEFT JOIN orders o ON o.user_id = u.id
GROUP BY u.id, u.name;

COMMENT ON TABLE users IS '用户主表：账号、联系方式与账号状态';
COMMENT ON TABLE products IS '商品表：价格、库存与 JSON 规格（颜色/尺寸/保修）';
COMMENT ON TABLE orders IS '订单表：下单用户、商品、数量、金额与订单状态';
COMMENT ON TABLE categories IS '分类表：自引用树形结构（parent_id 指向自身 id）';
COMMENT ON VIEW v_order_stats IS '订单统计视图：每用户的订单数、总消费与客单价';
COMMENT ON COLUMN users.status IS '账号状态：active=正常 / inactive=停用';
COMMENT ON COLUMN products.specs IS '商品规格 JSON：color/dimensions/warranty_months';
COMMENT ON COLUMN orders.order_status IS '订单状态：pending/paid/shipped/completed/cancelled';
"""

MYSQL_DDL = """
DROP TABLE IF EXISTS orders, categories, products, users;
CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(200) NOT NULL,
  phone VARCHAR(20),
  status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT '账号状态: active/inactive',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT='用户主表：账号、联系方式与账号状态';
CREATE TABLE products (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  stock INT NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'on_sale',
  specs JSON COMMENT '商品规格 JSON：color/dimensions/warranty_months'
) COMMENT='商品表：价格、库存与 JSON 规格';
CREATE TABLE orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  total_amount DECIMAL(10,2) NOT NULL,
  order_status VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT '订单状态：pending/paid/shipped/completed/cancelled',
  user_order_dt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_product FOREIGN KEY (product_id) REFERENCES products(id)
) COMMENT='订单表：下单用户、商品、数量、金额与订单状态';
CREATE TABLE categories (
  id INT AUTO_INCREMENT PRIMARY KEY,
  parent_id INT NULL,
  name VARCHAR(100) NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES categories(id)
) COMMENT='分类表：自引用树形结构（parent_id 指向自身 id）';
CREATE OR REPLACE VIEW v_order_stats AS
SELECT u.id AS user_id, u.name AS user_name,
       count(o.id) AS order_count,
       ROUND(SUM(o.total_amount), 2) AS total_spent,
       ROUND(AVG(o.total_amount), 2) AS avg_order
FROM users u LEFT JOIN orders o ON o.user_id = u.id
GROUP BY u.id, u.name;
"""

PG_SPECS_SQL = """
UPDATE products SET specs = jsonb_build_object(
  'color', (ARRAY['黑','白','银','蓝'])[1 + (id % 4)],
  'dimensions', jsonb_build_object('weight_kg', round((0.1 + id * 0.15)::numeric, 2)),
  'warranty_months', 12 + id * 6
);
"""

MYSQL_SPECS_SQL = """
UPDATE products SET specs = JSON_OBJECT(
  'color', ELT(1 + (id % 4), '黑', '白', '银', '蓝'),
  'dimensions', JSON_OBJECT('weight_kg', ROUND(0.1 + id * 0.15, 2)),
  'warranty_months', 12 + id * 6
);
"""

COUNT_SQL = "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM products), (SELECT count(*) FROM orders), (SELECT count(*) FROM categories)"


def _env(prefix: str, key: str) -> str | int:
    value = os.environ.get(f"DB_ASSISTANT_DEMO_{prefix}_{key.upper()}")
    if value is None:
        defaults = PG_DEFAULTS if prefix == "PG" else MYSQL_DEFAULTS
        return defaults[key]
    return int(value) if key == "port" else value


async def seed_postgres() -> dict[str, int]:
    import asyncpg

    conn = await asyncpg.connect(
        host=_env("PG", "host"), port=_env("PG", "port"),
        user=_env("PG", "user"), password=_env("PG", "password"),
        database=_env("PG", "db"),
    )
    try:
        await conn.execute(PG_DDL)
        await conn.execute(INSERT_USERS)
        await conn.execute(INSERT_PRODUCTS)
        await conn.execute(INSERT_ORDERS)
        await conn.execute(INSERT_CATEGORIES)
        await conn.execute(PG_SPECS_SQL)
        row = await conn.fetchrow(COUNT_SQL)
        return {"users": row[0], "products": row[1], "orders": row[2], "categories": row[3]}
    finally:
        await conn.close()


async def seed_mysql() -> dict[str, int]:
    import aiomysql

    conn = await aiomysql.connect(
        host=_env("MYSQL", "host"), port=_env("MYSQL", "port"),
        user=_env("MYSQL", "user"), password=_env("MYSQL", "password"),
        db=_env("MYSQL", "db"), autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            for stmt in (MYSQL_DDL, INSERT_USERS, INSERT_PRODUCTS, INSERT_ORDERS, INSERT_CATEGORIES, MYSQL_SPECS_SQL):
                await cur.execute(stmt)
            await cur.execute(COUNT_SQL)
            row = await cur.fetchone()
            return {"users": row[0], "products": row[1], "orders": row[2], "categories": row[3]}
    finally:
        conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-pg", action="store_true", help="跳过 PostgreSQL")
    parser.add_argument("--skip-mysql", action="store_true", help="跳过 MySQL")
    args = parser.parse_args()

    exit_code = 0
    if not args.skip_pg:
        try:
            counts = await seed_postgres()
            print(f"[ok] PostgreSQL seeded: {counts}")
        except Exception as exc:  # noqa: BLE001
            print(f"[err] PostgreSQL seed 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
            exit_code = 1
    if not args.skip_mysql:
        try:
            counts = await seed_mysql()
            print(f"[ok] MySQL seeded: {counts}")
        except Exception as exc:  # noqa: BLE001
            print(f"[err] MySQL seed 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
