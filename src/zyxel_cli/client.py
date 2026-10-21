from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import CookieJar
import random
import re
import string
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


class ZyxelError(RuntimeError):
    """Base error raised by this package."""


class AuthenticationError(ZyxelError):
    """The switch rejected or could not accept the login."""


class ProtocolError(ZyxelError):
    """The web UI returned data this client cannot safely interpret."""


def encode_password(password: str, *, rng: random.Random | None = None) -> str:
    """Reproduce the lightweight password encoding used by the switch UI."""
    chooser = rng or random.SystemRandom()
    alphabet = string.digits + string.ascii_lowercase + string.ascii_uppercase
    shift = len(password)
    encoded = "".join(chooser.choice(alphabet) + chr(ord(char) - shift) for char in password)
    return encoded + chooser.choice(alphabet)


def _scalar(js: str, *names: str) -> int | None:
    for name in names:
        match = re.search(rf"\b{re.escape(name)}\s*=\s*['\"]?(\d+)['\"]?\s*;", js, re.I)
        if match:
            return int(match.group(1))
    return None


def _array(js: str, *names: str) -> list[int] | None:
    for name in names:
        match = re.search(rf"\b{re.escape(name)}\s*=\s*\[([^\]]*)\]\s*;", js, re.I | re.S)
        if match:
            values = re.findall(r"\d+", match.group(1))
            return [int(value) for value in values]
    return None


@dataclass(frozen=True)
class PortConfig:
    state_mask: int
    poe_mask: int
    flow_control_mask: int
    speeds: tuple[int, ...]

    @classmethod
    def from_javascript(cls, js: str, *, port_count: int = 5) -> "PortConfig":
        state = _scalar(js, "portState", "port_state", "g_port_state")
        poe = _scalar(js, "portPoE", "portPoe", "port_poe", "g_port_poe")
        flow = _scalar(
            js,
            "portFlowCtrl",
            "portFlowControl",
            "portFlctl",
            "portFlwcl",
            "port_flowctrl",
            "g_port_flwcl",
        )
        speeds = _array(js, "portSpeed", "port_speed", "g_port_speed")
        if speeds is None:
            numbered = [_scalar(js, f"g_port_speed{i}", f"portSpeed{i}") for i in range(port_count)]
            if all(value is not None for value in numbered):
                speeds = [int(value) for value in numbered if value is not None]

        missing = []
        if state is None:
            missing.append("port state")
        if poe is None:
            missing.append("PoE state")
        if flow is None:
            missing.append("flow control")
        if speeds is None or len(speeds) < port_count:
            missing.append("port speeds")
        if missing:
            raise ProtocolError(
                "cannot safely parse " + ", ".join(missing) + " from port_state_data.js"
            )
        return cls(state, poe, flow, tuple(speeds[:port_count]))

    def with_ports(self, ports: Iterable[int], enabled: bool) -> "PortConfig":
        mask = sum(1 << (port - 1) for port in ports)
        if enabled:
            state = self.state_mask | mask
            poe = self.poe_mask | mask
        else:
            state = self.state_mask & ~mask
            poe = self.poe_mask & ~mask
        return PortConfig(state, poe, self.flow_control_mask, self.speeds)

    def with_port_changes(
        self, *, enable: Iterable[int] = (), disable: Iterable[int] = ()
    ) -> "PortConfig":
        enabled_mask = sum(1 << (port - 1) for port in enable)
        disabled_mask = sum(1 << (port - 1) for port in disable)
        return PortConfig(
            (self.state_mask | enabled_mask) & ~disabled_mask,
            (self.poe_mask | enabled_mask) & ~disabled_mask,
            self.flow_control_mask,
            self.speeds,
        )

    def form_data(self) -> dict[str, int]:
        data = {
            "g_port_state": self.state_mask,
            "g_port_poe": self.poe_mask,
            "g_port_flwcl": self.flow_control_mask,
        }
        data.update({f"g_port_speed{i}": speed for i, speed in enumerate(self.speeds)})
        return data


class ZyxelClient:
    def __init__(
        self,
        host: str,
        password: str,
        *,
        timeout: float = 8.0,
        retries: int = 2,
        port_count: int = 5,
    ) -> None:
        if "://" not in host:
            host = "http://" + host
        parsed = urlparse(host)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"invalid switch address: {host!r}")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.password = password
        self.timeout = timeout
        self.retries = retries
        self.port_count = port_count
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.logged_in = False

    def __enter__(self) -> "ZyxelClient":
        self.login()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.logout()

    def _request(self, path: str, data: dict[str, object] | None = None) -> str:
        body = urlencode(data).encode("ascii") if data is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            headers={"User-Agent": "zyxel-cli/0.1", "Accept": "*/*"},
        )
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except (HTTPError, URLError, TimeoutError, ConnectionError) as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
        raise ZyxelError(f"request to {path} failed: {last_error}")

    def login(self) -> None:
        response = self._request("/login.cgi", {"password": encode_password(self.password)})
        tokens = [cookie for cookie in self.cookie_jar if cookie.name == "token"]
        if tokens:
            self.logged_in = True
            return
        if "logged in already" in response.lower():
            raise AuthenticationError(
                "the switch already has an active web session; log out of the browser and retry"
            )
        if "password" in response.lower() or "login.cgi" in response.lower():
            raise AuthenticationError("login failed; check the password")
        # Older firmware did not issue a token cookie. Validate it by reading state.
        state = self._request("/port_state_data.js")
        if "login.cgi" in state.lower():
            raise AuthenticationError("login failed; check the password")
        self.logged_in = True

    def logout(self) -> None:
        if not self.logged_in:
            return
        try:
            self._request("/logout.html")
        except ZyxelError:
            pass
        finally:
            self.logged_in = False
            self.cookie_jar.clear()

    def get_port_config(self) -> PortConfig:
        js = self._request("/port_state_data.js")
        if "login.cgi" in js.lower() or "logged in already" in js.lower():
            raise AuthenticationError("the switch session expired")
        return PortConfig.from_javascript(js, port_count=self.port_count)

    def set_port_states(
        self,
        *,
        enable: Iterable[int] = (),
        disable: Iterable[int] = (),
        dry_run: bool = False,
    ) -> tuple[PortConfig, PortConfig]:
        enable = tuple(sorted(set(enable)))
        disable = tuple(sorted(set(disable)))
        overlap = set(enable) & set(disable)
        if overlap:
            raise ValueError(f"ports cannot be both enabled and disabled: {sorted(overlap)}")
        ports = enable + disable
        invalid = [port for port in ports if port < 1 or port > 4]
        if not ports:
            raise ValueError("at least one port change is required")
        if invalid:
            raise ValueError(f"GS1200-5HP v2 PoE ports are 1-4; invalid: {invalid}")

        before = self.get_port_config()
        proposed = before.with_port_changes(enable=enable, disable=disable)
        if dry_run or proposed == before:
            return before, proposed

        write_error: ZyxelError | None = None
        try:
            self._request("/port_state_set.cgi", proposed.form_data())
        except ZyxelError as error:
            # Some firmware applies the form and then closes the HTTP response.
            # Always attempt read-back before deciding the write failed.
            write_error = error

        try:
            after = self.get_port_config()
        except ZyxelError as first_error:
            # GS1200's web server occasionally stops answering within an
            # authenticated session after a write. Release it and verify using
            # a fresh login, as a manually issued follow-up command would.
            try:
                self.logout()
                time.sleep(1.0)
                self.login()
                after = self.get_port_config()
            except ZyxelError as recovery_error:
                detail = write_error or first_error
                raise ProtocolError(
                    "the write was sent but its result could not be verified; "
                    f"check status before retrying ({detail}; recovery: {recovery_error})"
                ) from recovery_error
        enabled_mask = sum(1 << (port - 1) for port in enable)
        disabled_mask = sum(1 << (port - 1) for port in disable)
        if (
            after.state_mask & enabled_mask != enabled_mask
            or after.poe_mask & enabled_mask != enabled_mask
            or after.state_mask & disabled_mask
            or after.poe_mask & disabled_mask
        ):
            raise ProtocolError("the switch accepted the request but read-back verification failed")
        return before, after

    def set_ports(self, ports: Iterable[int], enabled: bool, *, dry_run: bool = False) -> tuple[PortConfig, PortConfig]:
        changes = {"enable" if enabled else "disable": ports}
        return self.set_port_states(dry_run=dry_run, **changes)
