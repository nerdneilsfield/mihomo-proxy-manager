"""CLI 入口点，支持 ``python -m mihomo_proxy_manager`` 调用。

CLI entry point, supporting ``python -m mihomo_proxy_manager`` invocation.
"""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
