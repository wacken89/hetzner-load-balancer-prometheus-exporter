import datetime
import logging
import time
import os
import signal
import sys
from typing import Optional, Any
from prometheus_client import start_http_server, Gauge, Info
import requests
from pathlib import Path

EXPORTER_VERSION = '1.7.0'
HTTP_TIMEOUT = 15  # seconds for every Hetzner API call
PER_PAGE = 50  # maximum page size allowed by the Hetzner API
STEP_SECONDS = 60  # metrics resolution in seconds
METRICS_WINDOW_STEPS = 3  # how many steps back to request (we only use the latest point)
RATE_LIMIT_MAX_WAIT = 60  # cap for how long we back off on HTTP 429
API_MAX_RETRIES = 3  # retries for a single request when rate limited

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)s %(message)s',
)
log = logging.getLogger('hetzner-lb-exporter')


def load_env(var: str, default: Optional[Any] = None) -> Optional[str]:
    if var in os.environ:
        return os.environ[var]
    path_var = f"{var}_PATH"
    if path_var not in os.environ:
        if default is None:
            raise KeyError(f"Neither variable '{var}' nor '{path_var}' are defined")
        log.info("Variable '%s' is not defined, using default '%s'", var, default)
        return default
    path = Path(os.environ[path_var])
    try:
        with path.open("r", encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError as error:
        if default is None:
            raise KeyError(f"Missing secret file '{path}' specified for '{path_var}'") from error
        log.warning("Missing secret file for '%s', using default '%s'", path_var, default)
        return default


try:
    load_balancer_ids = load_env('LOAD_BALANCER_IDS')
    access_token = load_env('ACCESS_TOKEN')
    SCRAPE_INTERVAL = int(load_env('SCRAPE_INTERVAL', 30))
except KeyError as error:
    log.error(str(error)[1:-1])
    sys.exit(1)


HETZNER_CLOUD_API_URL_BASE = 'https://api.hetzner.cloud/v1'
HETZNER_CLOUD_API_URL_LB = f'{HETZNER_CLOUD_API_URL_BASE}/load_balancers/'
HETZNER_CLOUD_API_URL_SERVER = f'{HETZNER_CLOUD_API_URL_BASE}/servers/'

_HEADERS = {
    'Content-type': "application/json",
    'Authorization': f"Bearer {access_token}",
}

# Set to False at the start of every scrape cycle so we refresh the server-name
# cache at most once per cycle even if several targets miss the cache.
_server_cache_refreshed = False
_running = True


def _rate_limit_wait(response: requests.Response) -> float:
    """Seconds to wait after a 429, derived from Hetzner's rate-limit headers."""
    retry_after = response.headers.get('Retry-After')
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), RATE_LIMIT_MAX_WAIT)
        except ValueError:
            pass
    reset = response.headers.get('RateLimit-Reset')
    if reset:
        try:
            wait = float(reset) - time.time()
            return min(max(wait, 1.0), RATE_LIMIT_MAX_WAIT)
        except ValueError:
            pass
    return min(RATE_LIMIT_MAX_WAIT, 5.0)


def _api_get(url: str, params: Optional[dict] = None) -> dict:
    """Single place to do HTTP GET against the Hetzner API.

    Handles transport errors, HTTP errors and rate limiting (HTTP 429 with a
    bounded back-off using the RateLimit-Reset/Retry-After headers). Always
    returns a dict (empty dict on any failure). The timeout prevents the
    exporter from hanging forever if the Hetzner API stalls.
    """
    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=_HEADERS, params=params, timeout=HTTP_TIMEOUT)
        except requests.RequestException as error:
            log.error("HTTP request to %s failed: %s", url, error)
            return {}
        if response.status_code == 429:
            wait = _rate_limit_wait(response)
            log.warning("Rate limited on %s (attempt %d/%d), waiting %.1fs",
                        url, attempt, API_MAX_RETRIES, wait)
            time.sleep(wait)
            continue
        if response.status_code >= 400:
            log.error("%s from %s: %s", response.status_code, url, response.text[:300])
            return {}
        try:
            return response.json()
        except ValueError as error:
            log.error("Could not decode JSON from %s: %s", url, error)
            return {}
    log.error("Giving up on %s after %d rate-limited attempts", url, API_MAX_RETRIES)
    return {}


def _api_get_all(url: str, key: str, params: Optional[dict] = None) -> list:
    """GET a paginated collection, following meta.pagination.next_page.

    The Hetzner API returns at most 25 entries per page by default (50 max), so
    fetching only the first page silently drops resources once a project grows
    past that. This walks every page and concatenates the results.
    """
    params = dict(params or {})
    params["per_page"] = PER_PAGE
    items: list = []
    page: Optional[int] = 1
    while page:
        params["page"] = page
        data = _api_get(url, params=params)
        items.extend(data.get(key, []))
        pagination = (data.get("meta") or {}).get("pagination") or {}
        page = pagination.get("next_page")
    return items


def get_all_load_balancers_ids() -> list:
    return _api_get_all(HETZNER_CLOUD_API_URL_LB, 'load_balancers')


def get_load_balancer_info(lbid) -> dict:
    return _api_get(f'{HETZNER_CLOUD_API_URL_LB}{lbid}')


def get_all_server_names() -> dict:
    """Build a map of {server_id: server_name}, following pagination.

    Closes #28: when the project has no servers, or the API returns an
    unexpected payload, Hetzner sometimes omits the 'servers' key entirely.
    We fall back to an empty mapping instead of crashing.
    """
    servers = _api_get_all(HETZNER_CLOUD_API_URL_SERVER, 'servers')
    if not servers:
        log.warning("No 'servers' found in Hetzner response — "
                    "starting with empty server name cache")
        return {}
    return {x['id']: x['name'] for x in servers}


def get_server_name_from_cache(server_id) -> str:
    global server_name_cache, _server_cache_refreshed
    if server_id in server_name_cache:
        return server_name_cache[server_id]
    # Refresh the cache at most once per scrape cycle before giving up.
    if not _server_cache_refreshed:
        server_name_cache = get_all_server_names()
        _server_cache_refreshed = True
    return server_name_cache.get(server_id, str(server_id))


def get_metrics(metric_types: list, lbid) -> dict:
    """Fetch one or more metric types for a load balancer in a single request.

    The Hetzner metrics endpoint accepts 'type' as an array, so all series
    (open_connections, bandwidth, ...) can be retrieved with one API call
    instead of one call per type — meaningfully cheaper against the hourly
    rate limit. Only the latest datapoint is used, so we ask for a short
    window rather than a full hour.
    """
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    start = (now - datetime.timedelta(seconds=STEP_SECONDS * METRICS_WINDOW_STEPS)).isoformat()
    end = now.isoformat()
    url = f"{HETZNER_CLOUD_API_URL_LB}{lbid}/metrics"
    params = {
        "type": metric_types,  # requests serialises a list as repeated ?type=a&type=b
        "start": start,
        "end": end,
        "step": STEP_SECONDS,
    }
    data = _api_get(url, params=params)
    if "metrics" not in data:
        log.warning("No 'metrics' key in response for %s on LB %s", metric_types, lbid)
    return data


def extract_latest_metric_value(payload: dict, series_name: str) -> Optional[float]:
    """Pull the latest [timestamp, value] datapoint for a given series.

    Closes #27: if the time series is missing or empty, return None instead
    of raising IndexError / KeyError.
    """
    try:
        values = payload["metrics"]["time_series"][series_name]["values"]
    except (KeyError, TypeError):
        return None
    if not values:
        return None
    last = values[-1]  # use the latest, not the first, datapoint
    if not last or len(last) < 2:
        return None
    try:
        return float(last[1])
    except (TypeError, ValueError):
        return None


def set_gauge(gauge: Gauge, labels: dict, value: Optional[float]) -> None:
    """Set a gauge value, skipping when no data is available."""
    if value is None:
        return
    gauge.labels(**labels).set(value)


def _supports_color() -> bool:
    """Enable ANSI colours only on a real terminal (and honour NO_COLOR)."""
    return sys.stdout.isatty() and 'NO_COLOR' not in os.environ


_C = {
    'reset': '\033[0m', 'bold': '\033[1m', 'dim': '\033[2m',
    'cyan': '\033[36m', 'green': '\033[32m', 'yellow': '\033[33m',
    'blue': '\033[34m', 'magenta': '\033[35m',
} if _supports_color() else {k: '' for k in (
    'reset', 'bold', 'dim', 'cyan', 'green', 'yellow', 'blue', 'magenta')}


def print_banner() -> None:
    line = '═' * 60
    print(f"{_C['cyan']}{line}{_C['reset']}")
    print(f"{_C['cyan']}  {_C['bold']}Hetzner Load Balancer Exporter{_C['reset']}"
          f"{_C['cyan']}  ·  v{EXPORTER_VERSION}{_C['reset']}")
    print(f"{_C['cyan']}{line}{_C['reset']}", flush=True)


def print_summary(load_balancers: list, server_count: int) -> None:
    """Pretty per-load-balancer table plus a compact runtime summary."""
    # load_balancers: list of (id, name, type, service_count)
    id_w = max([len('ID')] + [len(str(lb[0])) for lb in load_balancers])
    name_w = max([len('NAME')] + [len(str(lb[1])) for lb in load_balancers])
    type_w = max(len('TYPE'), 4)

    print(f"\n{_C['bold']}Discovered {len(load_balancers)} load balancer"
          f"{'s' if len(load_balancers) != 1 else ''}:{_C['reset']}\n")
    header = (f"  {_C['dim']}{'ID':<{id_w}}  {'NAME':<{name_w}}  "
              f"{'TYPE':<{type_w}}  SERVICES{_C['reset']}")
    print(header)
    print(f"  {_C['dim']}{'─' * (id_w + name_w + type_w + 20)}{_C['reset']}")
    for lb_id, name, lb_type, svc_count in load_balancers:
        type_colour = _C['green'] if lb_type == 'http' else _C['blue']
        print(f"  {lb_id!s:<{id_w}}  {_C['bold']}{name:<{name_w}}{_C['reset']}  "
              f"{type_colour}{lb_type:<{type_w}}{_C['reset']}  {svc_count}")

    print(f"\n  {_C['dim']}Server name cache :{_C['reset']} "
          f"{_C['green']}{server_count}{_C['reset']} servers")
    print(f"  {_C['dim']}Scrape interval   :{_C['reset']} {SCRAPE_INTERVAL}s")
    print(f"  {_C['dim']}Metrics endpoint  :{_C['reset']} "
          f"{_C['magenta']}http://localhost:8000/{_C['reset']}")
    print(f"{_C['cyan']}{'═' * 60}{_C['reset']}\n", flush=True)


def _handle_signal(signum, _frame) -> None:
    global _running
    log.info("Received signal %s, shutting down after current cycle ...", signum)
    _running = False


def _interruptible_sleep(seconds: int) -> None:
    """Sleep in short slices so SIGTERM/SIGINT is honoured promptly."""
    deadline = time.monotonic() + seconds
    while _running and time.monotonic() < deadline:
        time.sleep(min(1.0, deadline - time.monotonic()))


if __name__ == '__main__':
    print_banner()
    log.info('Starting up, querying Hetzner Cloud API ...')

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if load_balancer_ids.lower() == 'all':
        load_balancer_ids_list = [lb['id'] for lb in get_all_load_balancers_ids()]
    else:
        load_balancer_ids_list = list(load_balancer_ids.split(","))

    load_balancer_full_list = []
    discovered = []  # richer rows (id, name, type, service_count) for the summary table
    for load_balancer_id in load_balancer_ids_list:
        load_balancer = get_load_balancer_info(load_balancer_id).get('load_balancer')
        if not load_balancer:
            log.error("Could not retrieve info for LB '%s'. Skipping.", load_balancer_id)
            continue
        try:
            load_balancer_name = load_balancer['name']
        except KeyError as e:
            log.error("Couldn't get field %s", e)
            sys.exit(1)

        services = load_balancer.get('services', [])
        load_balancer_type = 'tcp'
        for service in services:
            if service.get('protocol') in ('http', 'https'):
                load_balancer_type = 'http'
                break

        load_balancer_full_list.append([load_balancer_id, load_balancer_name, load_balancer_type])
        discovered.append((load_balancer_id, load_balancer_name, load_balancer_type, len(services)))

    if not load_balancer_full_list:
        log.error("No load balancers could be loaded. Exiting.")
        sys.exit(1)

    server_name_cache = get_all_server_names()

    print_summary(discovered, len(server_name_cache))

    id_name_list = ['hetzner_load_balancer_id', 'hetzner_load_balancer_name']
    hetzner_load_balancer_info = Info('hetzner_load_balancer', 'Hetzner Load Balancer Exporter build info')
    hetzner_openconnections = Gauge('hetzner_load_balancer_open_connections', 'Open Connections on Hetzner Load Balancer', id_name_list)
    hetzner_connections_per_second = Gauge('hetzner_load_balancer_connections_per_second', 'Connections per Second on Hetzner Load Balancer', id_name_list)
    hetzner_requests_per_second = Gauge('hetzner_load_balancer_requests_per_second', 'Requests per Second on Hetzner Load Balancer', id_name_list)
    hetzner_bandwidth_in = Gauge('hetzner_load_balancer_bandwidth_in', 'Bandwidth in on Hetzner Load Balancer', id_name_list)
    hetzner_bandwidth_out = Gauge('hetzner_load_balancer_bandwidth_out', 'Bandwidth out on Hetzner Load Balancer', id_name_list)

    id_name_service_list = id_name_list + ['hetzner_target_id', 'hetzner_target_name', 'hetzner_target_port']
    hetzner_service_state = Gauge('hetzner_load_balancer_service_state', "Health status of Load Balancer's services", id_name_service_list)

    start_http_server(8000)
    log.info('Hetzner Load Balancer Exporter started, metrics on http://localhost:8000/')

    hetzner_load_balancer_info.info({'version': EXPORTER_VERSION, 'buildhost': 'wacken89'})

    while _running:
        # Reset per-cycle state: drop stale target series (removed/renamed
        # targets would otherwise linger forever) and allow one cache refresh.
        _server_cache_refreshed = False
        hetzner_service_state.clear()

        for load_balancer_id, lb_name, load_balancer_type in load_balancer_full_list:
            base_labels = {
                'hetzner_load_balancer_id': load_balancer_id,
                'hetzner_load_balancer_name': lb_name,
            }

            # Fetch every metric series in a single API call.
            metric_types = ['open_connections', 'connections_per_second', 'bandwidth']
            if load_balancer_type == 'http':
                metric_types.append('requests_per_second')
            metrics_payload = get_metrics(metric_types, load_balancer_id)

            set_gauge(hetzner_openconnections, base_labels,
                      extract_latest_metric_value(metrics_payload, 'open_connections'))
            set_gauge(hetzner_connections_per_second, base_labels,
                      extract_latest_metric_value(metrics_payload, 'connections_per_second'))
            if load_balancer_type == 'http':
                set_gauge(hetzner_requests_per_second, base_labels,
                          extract_latest_metric_value(metrics_payload, 'requests_per_second'))
            set_gauge(hetzner_bandwidth_in, base_labels,
                      extract_latest_metric_value(metrics_payload, 'bandwidth.in'))
            set_gauge(hetzner_bandwidth_out, base_labels,
                      extract_latest_metric_value(metrics_payload, 'bandwidth.out'))

            lb_info = get_load_balancer_info(load_balancer_id).get('load_balancer', {})
            targets = []
            for x in lb_info.get('targets', []):
                if x.get('type') in ('server', 'ip'):
                    targets.append(x)
                elif x.get('type') == 'label_selector':
                    targets.extend(x.get('targets', []))

            for target in targets:
                for health_status in target.get('health_status', []):
                    if target['type'] == 'ip':
                        target_id = target['ip']['ip']
                        target_name = target['ip']['ip']
                    else:
                        target_id = target['server']['id']
                        target_name = get_server_name_from_cache(target['server']['id'])

                    hetzner_service_state.labels(
                        hetzner_load_balancer_id=load_balancer_id,
                        hetzner_load_balancer_name=lb_name,
                        hetzner_target_id=target_id,
                        hetzner_target_name=target_name,
                        hetzner_target_port=health_status['listen_port'],
                    ).set(1 if health_status.get('status') == 'healthy' else 0)

        _interruptible_sleep(SCRAPE_INTERVAL)

    log.info('Hetzner Load Balancer Exporter stopped.')
