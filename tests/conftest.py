import sys
import os

# Add mcp/ to sys.path so tests can import metrics and ollama_mcp_server directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
