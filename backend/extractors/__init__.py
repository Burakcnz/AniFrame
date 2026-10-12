class BaseExtractor:
    name = "base"
    domains = []
    patterns = []

    @classmethod
    def can_handle(cls, url):
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname or ""
        hostname_lower = hostname.lower()
        for domain in cls.domains:
            if domain in hostname_lower:
                return True
        for pattern in cls.patterns:
            if pattern in url:
                return True
        return False

    def extract(self, url):
        raise NotImplementedError

    def get_formats(self, url):
        raise NotImplementedError


class ExtractorRegistry:
    _extractors = []

    @classmethod
    def register(cls, extractor_class):
        cls._extractors.append(extractor_class)

    @classmethod
    def find_extractor(cls, url):
        for ext_class in cls._extractors:
            if ext_class.can_handle(url):
                return ext_class()
        return None

    @classmethod
    def get_all(cls):
        return [e() for e in cls._extractors]


def _auto_register():
    import os
    import importlib
    current_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in os.listdir(current_dir):
        if fname.startswith("_") or fname == "base.py" or not fname.endswith(".py"):
            continue
        module_name = f"backend.extractors.{fname[:-3]}"
        try:
            importlib.import_module(module_name)
        except Exception:
            pass


_auto_register()
