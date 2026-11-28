from dataclasses import dataclass, field
import hashlib
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .store import setting

MODEL_SETTING = "model_connection"

@dataclass(frozen=True)
class Model:
    endpoint: str
    name: str
    key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        return cls.from_values(os.getenv("PREAD_LLM_URL", ""), os.getenv("PREAD_LLM_MODEL", ""))

    @classmethod
    def from_values(cls, endpoint, name, key=""):
        endpoint = endpoint.strip().rstrip("/")
        name = name.strip()
        location = urlsplit(endpoint)
        if location.scheme not in {"http", "https"} or not location.hostname or not name:
            raise ValueError("Set PREAD_LLM_URL and PREAD_LLM_MODEL to enable model requests")
        if location.scheme == "http" and location.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model endpoints require HTTPS")
        if location.username or location.password or location.query or location.fragment:
            raise ValueError("Model endpoint must be a base URL without credentials or a query")
        env_endpoint = os.getenv("PREAD_LLM_URL", "").strip().rstrip("/")
        return cls(endpoint, name, key.strip() or (os.getenv("PREAD_LLM_KEY", "") if endpoint == env_endpoint else ""))

    @classmethod
    def configured(cls, db):
        connection = setting(db, MODEL_SETTING)
        if connection:
            values = json.loads(connection)
            return cls.from_values(values["endpoint"], values["name"])
        return cls.from_env()

    def save_connection(self, db):
        db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)",
                   (MODEL_SETTING, json.dumps({"endpoint": self.endpoint, "name": self.name})))

    def identity(self):
        return hashlib.sha256(f"{self.endpoint}\n{self.name}".encode()).hexdigest()

    def complete(self, instructions, source):
        headers = {"Content-Type": "application/json"}
        payload = json.dumps({
            "model": self.name,
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": source}],
            "temperature": 0,
        }).encode()
        request = Request(f"{self.endpoint}/chat/completions", payload, headers, method="POST")
        if self.key:
            request.add_unredirected_header("Authorization", f"Bearer {self.key}")
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
