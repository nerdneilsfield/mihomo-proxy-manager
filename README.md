<div align="center">

# mihomo-proxy-manager

专业、轻量、异步优先的 Clash/Mihomo 订阅聚合上游服务。

[![CI](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/ci.yml)
[![Docker](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/docker.yaml/badge.svg)](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/docker.yaml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](https://github.com/nerdneilsfield/mihomo-proxy-manager)
[![License](https://img.shields.io/github/license/nerdneilsfield/mihomo-proxy-manager?color=blue)](https://github.com/nerdneilsfield/mihomo-proxy-manager/blob/main/LICENSE)
[![Stars](https://img.shields.io/github/stars/nerdneilsfield/mihomo-proxy-manager?style=social)](https://github.com/nerdneilsfield/mihomo-proxy-manager/stargazers)

[English](README_ZH.md) · [GitHub](https://github.com/nerdneilsfield/mihomo-proxy-manager) · [Issues](https://github.com/nerdneilsfield/mihomo-proxy-manager/issues)

</div>

## 项目简介

`mihomo-proxy-manager` 是一个面向 Clash/Mihomo 的代理订阅聚合服务。它从多个上游订阅源拉取节点，解析 YAML、share-link 和 base64 订阅内容，经过过滤、重命名、缓存和合并后，输出 Mihomo 兼容的 `proxy-providers` YAML。

它适合把多个机场订阅整理成稳定、可控、可观测的内部 provider 服务，尤其适合需要隐藏真实订阅地址、统一节点命名、按设备或场景拆分 provider 路由的使用方式。

## 功能亮点

- 多订阅源聚合：支持按 route 组合多个 source。
- Mihomo provider 输出：直接返回 `proxies:` 结构的 YAML。
- 多格式解析：支持 Clash/Mihomo YAML、`ss://`、`vmess://`、`vless://`、`trojan://`、`hysteria2://` 和 base64 订阅。
- 过滤与重命名：支持 source 层和 route 层的正则过滤、类型过滤、前后缀模板。
- 安全 HTTP 抓取：限制重定向、响应大小、私网地址访问，并避免 Cookie 跨请求泄露。
- 条件请求缓存：支持 ETag 和 Last-Modified，减少上游请求压力。
- 文件缓存：source 级 JSON 缓存，原子写入，默认 `0600` 权限。
- 定时刷新：支持 interval、cron、启动刷新和 jitter。
- 插件钩子：MVP 支持 `before_fetch` HTTP Action。
- CI/CD：GitHub Actions 测试 Python 3.12、3.13、3.14，并构建多架构 Docker 镜像。

## 快速开始

### 使用 pip

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
```

校验配置：

```bash
mpm check -c examples/config.toml
```

启动服务：

```bash
mpm serve -c examples/config.toml
```

手动刷新某个订阅源：

```bash
mpm refresh -c examples/config.toml airport_a
```

### 使用 Docker

镜像会由 GitHub Actions 推送到：

```text
ghcr.io/nerdneislfield/mihomo-proxy-manager
docker.io/${DOCKER_USERNAME}/mihomo-proxy-manager
```

运行时挂载自己的配置文件，并按需挂载缓存和日志目录：

```bash
docker run --rm \
  -p 8080:8080 \
  -v "$PWD/examples/config.toml:/app/config.toml:ro" \
  -v "$PWD/data:/app/data" \
  -v "$PWD/logs:/app/logs" \
  ghcr.io/nerdneislfield/mihomo-proxy-manager:latest
```

## 配置示例

```toml
[server]
host = "127.0.0.1"
port = 8080
health_path = "/healthz"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"
format = "auto"
parse_error = "skip"

[sources.airport_a.refresh]
interval = "1h"

[sources.airport_a.rename]
prefix = "[{source}] "

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
require_all_sources = false
```

请求 provider 路由后会得到：

```yaml
proxies:
  - name: "[airport_a] HK 01"
    type: vmess
    server: example.com
    port: 443
```

完整配置请参考 [examples/config.toml](examples/config.toml)。

## 安全提醒

- provider 路径是 bearer secret，请使用高熵随机路径。
- 生产环境请放在 HTTPS 或可信反向代理之后。
- 上游订阅 URL、插件 header、节点凭据都应视为敏感信息。
- 默认禁止访问私网地址；除非你明确知道风险，不要开启私网 URL 访问。
- 缓存文件包含代理节点凭据，请保护好运行目录权限。

## 开发

```bash
make install
make all
```

常用命令：

```bash
make test
make lint
make typecheck
make check
```

直接使用 pip 的 CI 路径：

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m ruff check .
python -m ty check
python -m pytest -q
```

## 许可证

本项目使用 [LICENSE](LICENSE) 中声明的许可证发布。
