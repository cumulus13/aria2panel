"""Minimal aria2 JSON-RPC client (HTTP)."""
import requests


class Aria2Error(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.message = message
        self.code = code


class Aria2ConnectionError(Aria2Error):
    """aria2 could not be reached or returned something that is not JSON-RPC."""


class Aria2Client:
    def __init__(self, url, secret="", timeout=10.0, verify=True):
        self.url = url
        self.secret = secret or ""
        self.timeout = timeout
        self.verify = verify
        self._session = requests.Session()

    # -- transport -----------------------------------------------------
    def _params(self, params):
        head = ["token:%s" % self.secret] if self.secret else []
        return head + list(params)

    def _post(self, payload):
        try:
            response = self._session.post(
                self.url, json=payload, timeout=self.timeout, verify=self.verify
            )
        except requests.RequestException as exc:
            raise Aria2ConnectionError(str(exc)) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise Aria2ConnectionError(
                "invalid response from aria2 (HTTP %s)" % response.status_code
            ) from exc

    def call(self, method, *params):
        payload = {
            "jsonrpc": "2.0",
            "id": "panel",
            "method": method if method.startswith("system.") else "aria2." + method,
            "params": self._params(params),
        }
        data = self._post(payload)
        if "error" in data:
            error = data["error"] or {}
            raise Aria2Error(error.get("message", "aria2 error"), error.get("code"))
        return data.get("result")

    def multicall(self, calls):
        """calls: [(method, [params...]), ...]. Returns one value per call, an
        Aria2Error instance (not raised) for the calls that failed."""
        methods = [
            {"methodName": "aria2." + name, "params": self._params(params)}
            for name, params in calls
        ]
        data = self._post(
            {"jsonrpc": "2.0", "id": "panel", "method": "system.multicall", "params": [methods]}
        )
        if "error" in data:
            error = data["error"] or {}
            raise Aria2Error(error.get("message", "aria2 error"), error.get("code"))
        out = []
        for item in data.get("result", []):
            if isinstance(item, dict) and "code" in item:
                out.append(Aria2Error(item.get("message", "aria2 error"), item.get("code")))
            else:
                out.append(item[0] if isinstance(item, list) and item else item)
        return out

    # -- queries -------------------------------------------------------
    def version(self):
        return self.call("getVersion")

    def global_stat(self):
        return self.call("getGlobalStat")

    def tell_active(self, keys=None):
        return self.call("tellActive", *([keys] if keys else []))

    def tell_waiting(self, offset, num, keys=None):
        return self.call("tellWaiting", offset, num, *([keys] if keys else []))

    def tell_stopped(self, offset, num, keys=None):
        return self.call("tellStopped", offset, num, *([keys] if keys else []))

    def tell_status(self, gid, keys=None):
        return self.call("tellStatus", gid, *([keys] if keys else []))

    def get_option(self, gid):
        return self.call("getOption", gid)

    def get_global_option(self):
        return self.call("getGlobalOption")

    def get_files(self, gid):
        return self.call("getFiles", gid)

    def get_peers(self, gid):
        return self.call("getPeers", gid)

    def get_servers(self, gid):
        return self.call("getServers", gid)

    # -- commands ------------------------------------------------------
    def add_uri(self, uris, options=None):
        return self.call("addUri", list(uris), options or {})

    def add_torrent(self, b64, uris=None, options=None):
        return self.call("addTorrent", b64, uris or [], options or {})

    def add_metalink(self, b64, options=None):
        return self.call("addMetalink", b64, options or {})

    def pause(self, gid):
        return self.call("pause", gid)

    def force_pause(self, gid):
        return self.call("forcePause", gid)

    def unpause(self, gid):
        return self.call("unpause", gid)

    def pause_all(self):
        return self.call("pauseAll")

    def unpause_all(self):
        return self.call("unpauseAll")

    def remove(self, gid):
        return self.call("remove", gid)

    def force_remove(self, gid):
        return self.call("forceRemove", gid)

    def remove_result(self, gid):
        return self.call("removeDownloadResult", gid)

    def purge(self):
        return self.call("purgeDownloadResult")

    def change_option(self, gid, options):
        return self.call("changeOption", gid, options)

    def change_global_option(self, options):
        return self.call("changeGlobalOption", options)
