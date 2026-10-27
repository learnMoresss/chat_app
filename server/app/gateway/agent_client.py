from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class AgentClient:
    """网关 → worker-agent。只发控制命令，不中转任务数据。"""

    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def health(self) -> Dict[str, Any]:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{self.base_url}/health")
            r.raise_for_status()
            return r.json()

    def list_plugins(self) -> List[Dict[str, Any]]:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{self.base_url}/v1/plugins", headers=self._headers())
            r.raise_for_status()
            return list(r.json().get("plugins") or [])

    def list_gpus(self) -> List[Dict[str, Any]]:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{self.base_url}/v1/gpus", headers=self._headers())
            r.raise_for_status()
            return list(r.json().get("gpus") or [])

    def start_instance(
        self,
        plugin_id: str,
        *,
        vram_mb: Optional[int] = None,
        preferred_gpu: Optional[int] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"plugin_id": plugin_id}
        if vram_mb is not None:
            body["vram_mb"] = vram_mb
        if preferred_gpu is not None:
            body["preferred_gpu"] = preferred_gpu
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(
                f"{self.base_url}/v1/instances/start",
                headers=self._headers(),
                json=body,
            )
            if r.status_code >= 400:
                raise RuntimeError(r.text)
            return r.json()

    def stop_instance(self, instance_id: str) -> Dict[str, Any]:
        with httpx.Client(timeout=30.0) as c:
            r = c.post(
                f"{self.base_url}/v1/instances/{instance_id}/stop",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    def unload_instance(self, instance_id: str) -> Dict[str, Any]:
        with httpx.Client(timeout=30.0) as c:
            r = c.post(
                f"{self.base_url}/v1/instances/{instance_id}/unload",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()
