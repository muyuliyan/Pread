from dataclasses import dataclass
import hashlib
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Model:
    endpoint: str
    name: str

    @classmethod
    def from_env(cls):
        endpoint = os.getenv("PREAD_LLM_URL", "").rstrip("/")
        name = os.getenv("PREAD_LLM_MODEL", "").strip()
        location = urlsplit(endpoint)
        if location.scheme not in {"http", "https"} or not location.hostname or not name:
            raise ValueError("Set PREAD_LLM_URL and PREAD_LLM_MODEL to enable model requests")
        if location.scheme == "http" and location.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model endpoints require HTTPS")
        if location.username or location.password or location.query or location.fragment:
            raise ValueError("Model endpoint must be a base URL without credentials or a query")
        return cls(endpoint, name)

    def identity(self):
        return hashlib.sha256(f"{self.endpoint}\n{self.name}".encode()).hexdigest()

    def complete(self, instructions, source):
        headers = {"Content-Type": "application/json"}
        if key := os.getenv("PREAD_LLM_KEY"):
            headers["Authorization"] = f"Bearer {key}"
        payload = json.dumps({
            "model": self.name,
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": source}],
            "temperature": 0,
        }).encode()
        request = Request(f"{self.endpoint}/chat/completions", payload, headers, method="POST")
        with urlopen(request, timeout=90) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Model response exceeds 2 MiB")
        try:
            text = json.loads(raw)["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise ValueError("Invalid model response") from error
        if not text:
            raise ValueError("The model returned empty text")
        return text
