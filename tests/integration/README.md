# 集成测试（真实数据库）

需要 Docker 起真实 PG + MySQL：

```bash
docker compose up -d
set DB_ASSISTANT_TEST_PG_HOST=127.0.0.1
set DB_ASSISTANT_TEST_PG_PORT=54329
set DB_ASSISTANT_TEST_PG_PASSWORD=test
set DB_ASSISTANT_TEST_MYSQL_PORT=33069
set DB_ASSISTANT_TEST_MYSQL_PASSWORD=test
python -m pytest tests/integration -m integration
```

未设置环境变量时自动跳过。

