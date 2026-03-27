"""
Shared pytest configuration and fixtures.
Adds the backend/ directory to sys.path so `app.*` imports work
without installing the package.
"""
import sys
import os

# Allow `from app.services.xxx import ...` in test files
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
