"""入口：python -m db_assistant_mcp [--transport stdio|streamable-http] [--host H] [--port P] [--config PATH]

默认 stdio 模式；HTTP 模式必须配置 [http] token_env（fail-closed）。
"""

from __future__ import annotations

import sys


def _usage() -> str:
    return (
        "用法: python -m db_assistant_mcp [选项]\n"
        "选项:\n"
        "  --transport stdio|streamable-http  传输模式（默认 stdio）\n"
        "  --host HOST                        HTTP 监听地址（默认取配置 [http].host，即 127.0.0.1）\n"
        "  --port PORT                        HTTP 监听端口（默认取配置 [http].port，即 8000）\n"
        "  --config PATH                      配置文件路径（或设 DB_ASSISTANT_CONFIG）\n"
        "  --version, -V                      输出版本\n"
        "  --help, -h                         显示帮助\n"
        "CLI 管理命令请使用 `db-assistant <command>`。"
    )


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream.isatty():
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    args = sys.argv[1:]
    if "--version" in args or "-V" in args:
        from db_assistant_mcp import __version__

        print(f"db-assistant-mcp {__version__}")
        return
    if "--help" in args or "-h" in args:
        print(_usage())
        return

    def _value(flag: str, default: str | None = None) -> str | None:
        for i, arg in enumerate(args):
            if arg == flag and i + 1 < len(args):
                return args[i + 1]
        return default

    transport = _value("--transport", "stdio")
    config_path = _value("--config")
    host = _value("--host")
    port_raw = _value("--port")

    if transport not in ("stdio", "streamable-http"):
        print(f"不支持的 transport: {transport!r}（可选 stdio | streamable-http）", file=sys.stderr)
        sys.exit(2)

    port: int | None = None
    if port_raw is not None:
        try:
            port = int(port_raw)
        except ValueError:
            print(f"非法端口: {port_raw!r}", file=sys.stderr)
            sys.exit(2)

    if transport == "stdio":
        from db_assistant_mcp.server import run_stdio

        run_stdio(config_path)
        return

    from db_assistant_mcp.errors import ConfigError
    from db_assistant_mcp.server import run_http

    try:
        run_http(config_path, host=host, port=port)
    except ConfigError as exc:
        print(f"错误: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"提示: {exc.hint}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
