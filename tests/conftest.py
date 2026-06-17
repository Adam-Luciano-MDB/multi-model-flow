import sys
import os
from unittest.mock import MagicMock

# Add mcp/ to sys.path so tests can import metrics and ollama_mcp_server directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

# fastmcp requires Python 3.10+. Stub it out so tests run on Python 3.9 without
# needing the real package. The server only uses FastMCP as a decorator host; all
# tested functions are plain callables that don't invoke fastmcp at runtime.
if "fastmcp" not in sys.modules:
    _fastmcp_stub = MagicMock()
    _fastmcp_stub.FastMCP.return_value.tool.return_value = lambda f: f
    sys.modules["fastmcp"] = _fastmcp_stub
