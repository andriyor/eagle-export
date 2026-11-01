import requests
from requests.adapters import HTTPAdapter, Retry

RETRY_STATUSES = {429, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Karakeep:
    def __init__(self, url, api_key, pause=2):
        self.base = url.rstrip("/") + "/api/v1"
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"
        # Dropbox can take longer to deliver a file than the server keeps a connection idle.
        self.session.headers["Connection"] = "close"
        # read=0: after a lost response the bookmark may exist, and a retry would duplicate it.
        retry = Retry(
            total=3,
            read=0,
            other=0,
            status_forcelist=RETRY_STATUSES,
            allowed_methods=None,
            backoff_factor=pause,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.base + path, timeout=300, **kwargs)
        if not response.ok:
            raise ApiError(response.status_code, response.text[:300])
        return response.json() if response.content else None

    def post_json(self, path, payload):
        return self.request("POST", path, json=payload)

    def upload(self, path, file_name, content_type):
        with open(path, "rb") as file:
            return self.request("POST", "/assets", files={"file": (file_name, file, content_type)})
