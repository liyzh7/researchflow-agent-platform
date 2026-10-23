"""Finite, approval-gated research workflows."""

import os

os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
