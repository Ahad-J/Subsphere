"""CORS middleware metadata holder."""
from __future__ import annotations


class CORSMiddleware:
    def __init__(self, app=None, **kwargs):
        self.app = app
        self.options = kwargs
