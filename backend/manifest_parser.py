import re
import requests


def parse_m3u8(url, timeout=15):
    """Fetch and parse an HLS master playlist, return quality levels sorted by bandwidth desc."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            return None
        content = resp.text
    except Exception:
        return None

    if not content.strip().startswith("#EXTM3U"):
        return None

    formats = []
    lines = content.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            info = line[len("#EXT-X-STREAM-INF:"):].strip()
            bandwidth = 0
            resolution = ""
            name = ""

            for attr in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', info):
                attr = attr.strip()
                if "=" in attr:
                    key, val = attr.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key == "BANDWIDTH":
                        bandwidth = int(val) if val.isdigit() else 0
                    elif key == "RESOLUTION":
                        resolution = val
                    elif key == "NAME":
                        name = val

            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith("#"):
                    media_url = _resolve_url(url, next_line)
                    label = name or resolution or f"{bandwidth // 1000}k"
                    formats.append({
                        "id": label.lower().replace(" ", ""),
                        "label": label,
                        "url": media_url,
                        "bandwidth": bandwidth,
                        "resolution": resolution,
                    })
                    i += 1
        i += 1

    if not formats:
        return None

    formats.sort(key=lambda x: x["bandwidth"], reverse=True)
    return formats


def parse_mpd(url, timeout=15):
    """Fetch and parse a DASH MPD manifest, return quality levels sorted by bandwidth desc."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            return None
        content = resp.text
    except Exception:
        return None

    try:
        from xml.dom import minidom
        dom = minidom.parseString(content)
    except Exception:
        return None

    formats = []
    representations = dom.getElementsByTagName("Representation")
    for rep in representations:
        bandwidth_str = rep.getAttribute("bandwidth")
        bandwidth = int(bandwidth_str) if bandwidth_str and bandwidth_str.isdigit() else 0
        width = rep.getAttribute("width")
        height = rep.getAttribute("height")
        mime_type = rep.getAttribute("mimeType") or ""

        base_url_elem = rep.getElementsByTagName("BaseURL")
        segment_url = ""
        if base_url_elem and base_url_elem[0].firstChild:
            segment_url = base_url_elem[0].firstChild.nodeValue

        if "video" not in mime_type.lower() and not width and not height:
            continue

        resolution = f"{width}x{height}" if width and height else ""
        label = resolution or f"{bandwidth // 1000}k"
        formats.append({
            "id": label.lower().replace(" ", ""),
            "label": label,
            "url": _resolve_url(url, segment_url) if segment_url else url,
            "bandwidth": bandwidth,
            "resolution": resolution,
        })

    if not formats:
        return None

    formats.sort(key=lambda x: x["bandwidth"], reverse=True)
    return formats


def _resolve_url(base, relative):
    if not relative:
        return base
    if relative.startswith("http://") or relative.startswith("https://"):
        return relative
    if relative.startswith("//"):
        scheme = base.split(":")[0] if "://" in base else "https"
        return f"{scheme}:{relative}"

    base_clean = base.split("?")[0].split("#")[0]
    if "/" in base_clean:
        base_dir = base_clean.rsplit("/", 1)[0] + "/"
        return base_dir + relative.lstrip("/")
    return base + "/" + relative.lstrip("/")
