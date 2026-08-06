.PHONY: build publish clean test lint

build:  ## 构建 wheel（优先 uv build；离线环境回退：python scripts/build_wheel.py）
	uv build

publish: build  ## 发布到 PyPI（需 UV_PUBLISH_TOKEN 或 uv 登录态）
	uv publish

clean:
	rm -rf dist build *.egg-info

lint:
	ruff check src tests

test:
	python -m pytest tests -m "not integration" -q
