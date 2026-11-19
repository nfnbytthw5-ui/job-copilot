"""适配器协议、加载与注册。

适配器是唯一允许产生副作用的地方。加载方式：
  - 内置：adapters/<name>/adapter.py 暴露 `Adapter` 类
  - 外部：config 中 platforms.<name>.module 指向任意可导入模块
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, Iterable

from .models import ApplyResult, Health, Job, Message, Query, RunContext


class AdapterProtocol(Protocol):
    name: str
    display_name: str

    def open(self, ctx: RunContext) -> None: ...
    def health(self) -> Health: ...
    def search(self, query: Query) -> Iterable[Job]: ...
    def apply(self, job: Job) -> ApplyResult: ...
    def inbox(self, since: str | None = None) -> Iterable[Message]: ...
    def close(self) -> None: ...


class AdapterLoadError(RuntimeError):
    pass


def load_adapter(name: str, spec: dict, repo_root: Path) -> AdapterProtocol:
    """按 config 中的声明加载适配器实例。"""
    module_path = spec.get("module")

    if module_path:
        path = Path(module_path)
        if not path.is_absolute():
            path = repo_root / path
        if not path.exists():
            raise AdapterLoadError(f"适配器文件不存在：{path}")
        mod_name = f"jobcopilot_adapter_{name}"
        module_spec = importlib.util.spec_from_file_location(mod_name, path)
        if module_spec is None or module_spec.loader is None:
            raise AdapterLoadError(f"无法加载适配器：{path}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[mod_name] = module
        module_spec.loader.exec_module(module)
    else:
        mod_name = f"adapters.{name}.adapter"
        try:
            module = importlib.import_module(mod_name)
        except ImportError as exc:
            raise AdapterLoadError(
                f"未找到适配器 {name}，请复制 adapters/_template 并实现"
            ) from exc

    cls = getattr(module, "Adapter", None)
    if cls is None:
        raise AdapterLoadError(f"适配器 {name} 缺少 Adapter 类")

    instance = cls(**spec.get("options", {}))
    instance.name = name
    if not hasattr(instance, "display_name"):
        instance.display_name = name
    return instance
