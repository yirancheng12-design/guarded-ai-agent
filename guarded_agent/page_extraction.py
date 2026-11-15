"""Offline extraction of already approved HTML; never downloads resources."""


def extract_main_text(html, url):
    """Return main text or None so the caller can retain its basic parser."""
    try:
        from trafilatura import extract
        return extract(html, url=url, include_comments=False, include_tables=True,
                       output_format='txt', fast=True) or None
    except Exception:
        # Optional dependency failures must not break ordinary page reading.
        return None
