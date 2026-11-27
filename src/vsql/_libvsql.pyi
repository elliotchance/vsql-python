# The extension module (_libvsql.c) is private, so its functions and constants
# aren't typed.

from typing import Any

def __getattr__(name: str) -> Any: ...
