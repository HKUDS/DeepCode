
from typing import Callable, Dict, Any

class ToolRegistry:
    """Registry for managing custom tools that agents can call."""

    def __init__(self):
        self._tools: Dict[str, Callable[..., Any]] = {}

    def register_tool(self, name: str, func: Callable[..., Any]):
        if name in self._tools:
            raise ValueError(f"Tool '{name}' already exists.")
        self._tools[name] = func

    def unregister_tool(self, name: str):
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found.")
        del self._tools[name]

    def get_tool(self, name: str) -> Callable[..., Any]:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found.")
        return self._tools[name]

    def list_tools(self):
        return list(self._tools.keys())


# global instance
registry = ToolRegistry()


def tool(name: str):
    """Decorator for easy tool registration."""
    def decorator(func: Callable[..., Any]):
        registry.register_tool(name, func)
        return func
    return decorator


# Example: custom tool added by user
@tool("hello_world")
def hello_tool(name: str = "developer") -> str:
    return f"Hello, {name}! This is a custom tool hook."


if __name__ == "__main__":
    # Demonstration
    print("Registered tools:", registry.list_tools())
    result = registry.get_tool("hello_world")("DeepCode")
    print(result)
