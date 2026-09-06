import base64
import gzip
import hashlib
import json
import os
import random
import time
from typing import Any, Dict, List, Optional
import httpx

from .config import Config, NO_TOKEN_MESSAGE


def sign_data(data: Dict[str, Any], uri_path: str, secret_key: str) -> str:
    """Computes the Viwoods Cloud MD5 request signature."""
    data_copy = dict(data)
    data_copy["uri"] = "/" + uri_path.lstrip("/")
    sorted_keys = sorted(data_copy.keys())
    parts = []
    for k in sorted_keys:
        v = data_copy[k]
        parts.append(f"{k}={v}")
    sig_str = "&".join(parts) + secret_key
    return hashlib.md5(sig_str.encode("utf-8")).hexdigest()


def decode_gzip_b64(b64_str: str) -> Any:
    """Decodes a Base64-encoded, Gzip-compressed JSON payload from Viwoods Cloud."""
    if not b64_str or not isinstance(b64_str, str):
        return None
    try:
        compressed = base64.b64decode(b64_str)
        decompressed = gzip.decompress(compressed)
        return json.loads(decompressed.decode("utf-8"))
    except Exception as e:
        # If not gzip, maybe plain json or string
        try:
            return json.loads(b64_str)
        except Exception:
            return b64_str


def solve_slider_captcha():
    """
    Simulates the Viwoods frontend slider verification tokens.
    Reverse-engineered from cloud.viwoods.com webpack module 2580.
    """
    # 32 random hex characters
    secret = secrets_hex(16)
    nonce = secrets_hex(16)
    start_time = int(time.time() * 1000)
    duration = random.randint(950, 1850)
    issued_at = start_time + duration

    # Build simulated mouse samples matching human drag motion
    # Track width ~300px, max offset ~260px
    samples = []
    step_count = random.randint(15, 25)
    current_x = 0
    current_t = start_time

    for i in range(step_count):
        progress = i / (step_count - 1)
        # S-curve ease in-out
        offset = 260 * (progress ** 2 * (3 - 2 * progress)) + random.uniform(-1, 1)
        current_x = max(0, offset)
        current_t = start_time + int(duration * progress)
        samples.append({"x": current_x, "t": current_t})

    # FNV-1a 32-bit hash function as implemented in Viwoods frontend
    def fnv1a(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h + (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24)) & 0xFFFFFFFF
        return f"{h:08x}"

    # Trace hash
    diffs = []
    for s_idx, sample in enumerate(samples):
        if s_idx == 0:
            diffs.append("0:0")
        else:
            prev = samples[s_idx - 1]
            dx = round(sample["x"] - prev["x"])
            dt = max(0, sample["t"] - prev["t"])
            diffs.append(f"{dx}:{dt}")
    trace_hash = fnv1a("|".join(diffs))

    # Signature
    sig_input = f"{secret}:{nonce}:{trace_hash}:{duration}:{issued_at}"
    sig = fnv1a(sig_input)

    return {
        "sliderVerifyKey": secret,
        "sliderVerifyNonce": nonce,
        "sliderVerifyTrace": trace_hash,
        "sliderVerifyDuration": duration,
        "sliderVerifySig": sig
    }


def secrets_hex(nbytes: int) -> str:
    return "".join(f"{random.randint(0, 255):02x}" for _ in range(nbytes))


class ViwoodsClient:
    def __init__(self, config: Config):
        self.config = config
        self.http = httpx.Client(timeout=30.0)

    def _headers(self, data: Dict[str, Any], uri_path: str) -> Dict[str, str]:
        sign = sign_data(data, uri_path, self.config.secret_key)
        headers = {
            "Content-Type": "application/json",
            "System-Version": "1.0.0",
            "Source": "2",
            "Machine-Model": "web",
            "Machine-Number": self.config.machine_number or "",
            "Language": "en",
            "Sign": sign
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
            headers["Access-Token"] = self.config.token
        return headers

    def post(self, uri_path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Makes an authenticated, signed POST request to the Viwoods Cloud API."""
        if not self.config.token and not uri_path.rstrip("/").endswith("user/login"):
            raise RuntimeError(NO_TOKEN_MESSAGE)

        uri = uri_path.lstrip("/")
        payload = dict(data or {})
        payload["uri"] = "/" + uri

        headers = self._headers(payload, uri)

        url = f"{self.config.api_base_url}/{uri}"
        resp = self.http.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        res_json = resp.json()

        if res_json.get("code") not in (200, "200"):
            msg = res_json.get("msg") or res_json.get("message") or "API error"
            raise RuntimeError(f"Viwoods API error ({uri}): {msg} (code: {res_json.get('code')})")

        return res_json

    def login(self, email: str, password: str) -> str:
        """Logs into Viwoods Cloud using email and password with simulated slider verification."""
        captcha = solve_slider_captcha()
        data = {
            "email": email,
            "password": password,
            "source": "2",
            **captcha
        }
        res = self.post("api/v1/user/login", data)
        token = res.get("data", {}).get("token")
        if not token:
            raise RuntimeError("Login succeeded but no token was returned.")
        self.config.token = token
        return token

    def refresh_token(self) -> str:
        """Refreshes the active session JWT token."""
        res = self.post("api/v1/user/refreshLogin", {})
        token = res.get("data", {}).get("token")
        if token:
            self.config.token = token
            return token
        return self.config.token

    def get_devices(self) -> List[Dict[str, Any]]:
        """Lists user registered Viwoods devices."""
        res = self.post("api/v1/userDevice", {"pageSize": "100"})
        devices = res.get("data", {}).get("list", [])
        if devices and not self.config.machine_number:
            self.config.machine_number = devices[0].get("machineNumber", "")
            self.config.device_name = devices[0].get("modelName", "AiPaper")
        return devices

    def get_root_folders(self) -> List[Dict[str, Any]]:
        """Returns root folders / apps (Paper, Meeting, Learning, Knowledge Base, Memo)."""
        res = self.post("api/v1/resourceSync/getRootFolder", {})
        return res.get("data", [])

    def get_folder_items(
        self,
        app_type: int = 1,
        resource_id: str = "",
        sub_tab: int = -1,
        page_index: int = 1,
        page_size: int = 100
    ) -> List[Dict[str, Any]]:
        """Lists notebooks and subfolders inside a given folder."""
        data = {
            "pageIndex": page_index,
            "pageSize": page_size,
            "appType": app_type,
            "resourceId": resource_id or "",
            "isDelete": -1,
            "subTab": sub_tab,
            "resourceType": -1,
            "star": -1,
            "sortField": "last_modified_time",
            "sortOrder": "desc"
        }
        res = self.post("api/v1/resourceSync/getPage", data)
        return res.get("data", {}).get("list", [])

    APP_SYNC_ENDPOINTS = {
        1: "api/v1/paperSync/get",
        2: "api/v1/meetingSync/get",
        3: "api/v1/learningSync/get",
        4: "api/v1/knowledgeBaseSync/get",
        6: "api/v1/memoSync/get",
    }

    def get_paper_detail(self, uuid: str, app_type: int = 1) -> Dict[str, Any]:
        """Fetches full note details across Paper, Meeting, Learning, Knowledge Base, and Memo."""
        endpoint = self.APP_SYNC_ENDPOINTS.get(app_type, "api/v1/paperSync/get")
        res = self.post(endpoint, {"uuid": uuid})
        raw_data = res.get("data", {})
        if not isinstance(raw_data, dict):
            raw_data = {}

        parsed_meta = decode_gzip_b64(raw_data.get("meta", "")) or {}
        parsed_data = decode_gzip_b64(raw_data.get("data", "")) or {}
        if not isinstance(parsed_meta, dict):
            parsed_meta = {}
        if not isinstance(parsed_data, dict):
            parsed_data = {}

        # Meta payload container can be paperMeeting, knowledgeBase, memo, or meeting
        container = (
            parsed_meta.get("paperMeeting") or
            parsed_meta.get("knowledgeBase") or
            parsed_meta.get("memo") or
            parsed_meta.get("meeting") or
            {}
        )
        if not isinstance(container, dict):
            container = {}

        raw_pages = container.get("imagePages") or container.get("pages") or []
        if not raw_pages:
            raw_pages = parsed_data.get("pages") or parsed_data.get("pageList") or []
        if not raw_pages and isinstance(parsed_data.get("serviceImageFileInfo"), dict):
            furl = parsed_data["serviceImageFileInfo"].get("fileURL")
            if furl:
                raw_pages = [{"pageNo": 1, "imageUrl": furl}]

        normalized_pages = []
        for idx, p in enumerate(raw_pages, start=1):
            pno = p.get("pageNo") or p.get("pageNumber") or idx
            img = p.get("imageUrl") or p.get("pageImageUrl") or ""
            if not img and isinstance(p.get("cloudImageFileInfo"), dict):
                img = p["cloudImageFileInfo"].get("fileURL", "")
            if not img and isinstance(p.get("cloudCoverImageFileInfo"), dict):
                img = p["cloudCoverImageFileInfo"].get("fileURL", "")
            if not img and isinstance(p.get("handwriteImageFile"), dict):
                img = p["handwriteImageFile"].get("fileURL", "")

            cnt = p.get("content", "")
            if not cnt and isinstance(parsed_data.get("pageList"), list) and idx - 1 < len(parsed_data["pageList"]):
                cnt = parsed_data["pageList"][idx - 1].get("content", "")

            normalized_pages.append({
                "pageNo": pno,
                "imageUrl": img,
                "content": cnt
            })

        note_meta = parsed_data.get("note", {}) if isinstance(parsed_data.get("note"), dict) else {}
        memo_meta = parsed_data.get("memoFileInfo", {}) if isinstance(parsed_data.get("memoFileInfo"), dict) else {}

        name = (
            container.get("name") or
            note_meta.get("fileName") or
            memo_meta.get("fileName") or
            parsed_data.get("noteName") or
            parsed_data.get("imgName") or
            raw_data.get("name") or
            "Untitled"
        )
        last_mod = (
            note_meta.get("lastModifiedTime") or
            memo_meta.get("lastModifiedTime") or
            parsed_data.get("upTime") or
            parsed_data.get("lastEditDialogTime") or
            raw_data.get("updatedAt") or
            0
        )

        recordings = container.get("recordings", []) if isinstance(container.get("recordings"), list) else []

        return {
            "uuid": uuid,
            "appType": app_type,
            "raw": raw_data,
            "meta": parsed_meta,
            "data": parsed_data,
            "name": name,
            "imagePages": normalized_pages,
            "recordings": recordings,
            "lastModifiedTime": last_mod
        }

    def get_daily_list(self, start_date: str, end_date: str, app_type: int = 1) -> List[Dict[str, Any]]:
        """Fetches daily journal entries within a date range."""
        data = {
            "startDate": start_date,
            "endDate": end_date,
            "type": app_type,
            "isFinish": -1
        }
        res = self.post("api/v1/resourceDailySync/getList", data)
        return res.get("data", [])

    def get_daily_dates(self, month: str) -> List[str]:
        """Returns list of active daily dates for a month (YYYY-MM)."""
        res = self.post("api/v1/resourceDailySync/getDateList", {"month": month})
        return res.get("data", [])

    def download_file(self, url: str, target_path: str) -> bool:
        """Downloads a remote file (CloudFront image or audio) to a local path."""
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with self.http.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(target_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        return True
