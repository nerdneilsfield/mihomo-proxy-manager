"""插件模块，当前提供 HTTP Action 插件支持。

Plugin module, currently providing HTTP Action plugin support.
"""

from .http_action import HttpActionPlugin, PluginContext, PluginResult

__all__ = ["HttpActionPlugin", "PluginContext", "PluginResult"]
