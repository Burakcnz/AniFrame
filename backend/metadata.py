import re


def extract_metadata(filename, folder_path=""):
    name = filename.rsplit(".", 1)[0]
    result = {"season": 0, "episode": 0, "year": 0, "quality": "", "title": name}

    se_patterns = [
        r"[Ss](\d{1,2})\s*[Ee](\d{1,3})",
        r"[Ss]eason\s*(\d{1,2}).*?[Ee]pisode\s*(\d{1,3})",
        r"(\d{1,2})x(\d{1,2})",
        r"[Ss]ezon\s*(\d{1,2}).*?[Bb][öo]l[üu]m\s*(\d{1,3})",
    ]
    for pattern in se_patterns:
        m = re.search(pattern, name)
        if m:
            result["season"] = int(m.group(1))
            result["episode"] = int(m.group(2))
            break

    ep_patterns = [
        r"[Bb][öo]l[üu]m\s*(\d{1,3})",
        r"[Ee]pisode\s*(\d{1,3})",
        r"[Ee]p?\s*(\d{1,3})",
        r"#(\d{1,3})",
    ]
    if result["episode"] == 0:
        for pattern in ep_patterns:
            m = re.search(pattern, name)
            if m:
                result["episode"] = int(m.group(1))
                break

    season_patterns = [
        r"[Ss]eason\s*(\d{1,2})",
        r"[Ss]ezon\s*(\d{1,2})",
    ]
    if result["season"] == 0:
        for pattern in season_patterns:
            m = re.search(pattern, folder_path)
            if m:
                result["season"] = int(m.group(1))
                break

    if result["season"] == 0:
        for pattern in season_patterns:
            m = re.search(pattern, name)
            if m:
                result["season"] = int(m.group(1))
                break

    year_patterns = [
        r"\((\d{4})\)",
        r"\.(\d{4})\.",
        r"\[(\d{4})\]",
        r"\b(19\d{2}|20\d{2})\b",
    ]
    for pattern in year_patterns:
        m = re.search(pattern, name)
        if m:
            y = int(m.group(1))
            if 1900 <= y <= 2099:
                result["year"] = y
                break

    quality_patterns = [
        r"\b(4K|2160p)",
        r"\b(1080p|FHD)",
        r"\b(720p|HD)",
        r"\b(480p|SD)",
        r"\b(BluRay|BRRip|BDRip|Blu-ray)",
        r"\b(WebRip|WEB-DL|WEBRip|WEBDL)",
        r"\b(HDRip|HDR10|DV|DolbyVision|HDR)",
        r"\b(H264|H265|HEVC|AV1|AVC|x264|x265)",
    ]
    found_qualities = []
    for pattern in quality_patterns:
        m = re.search(pattern, name, re.IGNORECASE)
        if m:
            found_qualities.append(m.group(1))
    result["quality"] = ", ".join(found_qualities) if found_qualities else ""

    clean = name
    for rm in [
        r"\[.*?\]",
        r"\(.*?\)",
        r"\b\d{3,4}p\b",
        r"\b(BluRay|BRRip|BDRip|WebRip|WEB-DL|WEBRip|WEBDL|HDRip|HDR|DV|HDR10|DolbyVision|H264|H265|HEVC|AV1|AVC|x264|x265|FHD|SD|480p|2160p)\b",
    ]:
        clean = re.sub(rm, "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[.\-_]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip().strip(".-_ ")
    result["title"] = clean if clean else name

    return result
