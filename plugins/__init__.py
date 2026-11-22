"""Мини-система плагинов для D&D-стола.

Каждый модуль в этой папке может определить функцию ``register(app)``,
которая вызовется ровно один раз при старте ``MasterApp``. Внутри
``register`` плагин добавляет свои кнопки/вкладки и может хранить данные
в ``app.state.plugins[<name>]``.
"""
import importlib
import pkgutil

__all__ = ["load_plugins"]


def load_plugins(app):
    loaded = []
    for _finder, name, _ispkg in pkgutil.iter_modules(__path__):
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[plugins] не удалось импортировать {name}: {exc}")
            continue
        if not hasattr(mod, "register"):
            continue
        try:
            mod.register(app)
            loaded.append(name)
        except Exception as exc:  # noqa: BLE001
            print(f"[plugins] {name}.register упал: {exc}")
    print(f"[plugins] загружено: {', '.join(loaded) or '—'}")
    return loaded