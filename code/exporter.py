import datetime
import time
import os
import sys
from typing import Optional, Any
from prometheus_client import start_http_server, Gauge, Info
import requests
from pathlib import Path

EXPORTER_VERSION = '1.6.0'
HTTP_TIMEOUT = 15  # seconds for every Hetzner API call


def load_env(var: str, default: Optional[Any] = None) -> Optional[str]:
    if var in os.environ:
        return os.environ[var]
    path_var = f"{var}_PATH"
    if path_var not in os.environ:
        if default is None:
            raise KeyError(f"Neither variable '{var}' nor '{path_var}' are defined")
        print(f"Variable '{var}' is not defined, using default '{default}'")
        return default
    path = Path(os.environ[path_var])
    try:
        with path.open("r", encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError as error:
        if default is None:
            raise KeyError(f"Missing secret file '{path}' specified for '{path_var}'") from error
        print(f"Missing secret file for '{path_var}', using default '{default}'")
        return default


try:
    load_balancer_ids = load_env('LOAD_BALANCER_IDS')
    access_token = load_env('ACCESS_TOKEN')
    SCRAPE_INTERVAL = load_env('SCRAPE_INTERVAL', 30)
except KeyError as error:
    print(str(error)[1:-1])
    sys.exit(1)


HETZNER_CLOUD_API_URL_BASE = 'https://api.hetzner.cloud/v1'
HETZNER_CLOUD_API_URL_LB = f'{HETZNER_CLOUD_API_URL_BASE}/load_balancers/'
HETZNER_CLOUD_API_URL_SERVER = f'{HETZNER_CLOUD_API_URL_BASE}/servers/'

_HEADERS = {
    'Content-type': "application/json",
    'Authorization': f"Bearer {access_token}",
}


def _api_get(url: str, params: Optional[dict] = None) -> dict:
    """Single place to do HTTP GET against the Hetzner API.

    Raises for HTTP errors and always returns a dict (empty dict on JSON
    decoding failure). Adding a timeout prevents the exporter from hanging
    forever if the Hetzner API stalls.
    """
    try:
        response = requests.get(url, headers=_HEADERS, params=params, timeout=HTTP_TIMEOUT)
    except requests.RequestException as error:
        print(f"[ERROR] HTTP request to {url} failed: {error}")
        return {}
    if response.status_code >= 400:
        print(f"[ERROR] {response.status_code} from {url}: {response.text[:300]}")
        return {}
    try:
        return response.json()
    except ValueError as error:
        print(f"[ERROR] Could not decode JSON from {url}: {error}")
        return {}


def get_all_load_balancers_ids() -> list:
    data = _api_get(HETZNER_CLOUD_API_URL_LB)
    return data.get('load_balancers', [])


def get_load_balancer_info(lbid) -> dict:
    return _api_get(f'{HETZNER_CLOUD_API_URL_LB}{lbid}')


def get_all_server_names() -> dict:
    """Build a map of {server_id: server_name}.

    Closes #28: when the project has no servers, or the API returns an
    unexpected payload, Hetzner sometimes omits the 'servers' key entirely.
    We fall back to an empty mapping instead of crashing.
    """
    data = _api_get(HETZNER_CLOUD_API_URL_SERVER)
    servers = data.get('servers')
    if not servers:
        print("[WARN] No 'servers' found in Hetzner response — "
              "starting with empty server name cache")
        return {}
    return {x['id']: x['name'] for x in servers}


# Closes #22: get_server_info() was unused. Removed.


def get_server_name_from_cache(server_id) -> str:
    global server_name_cache
    if server_id in server_name_cache:
        return server_name_cache[server_id]
    # Refresh cache once before giving up
    server_name_cache = get_all_server_names()
    return server_name_cache.get(server_id, str(server_id))


def get_metrics(metrics_type: str, lbid) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    start = (now - datetime.timedelta(hours=1)).isoformat()
    end = now.isoformat()
    url = f"{HETZNER_CLOUD_API_URL_LB}{lbid}/metrics"
    params = {
        "type": metrics_type,
        "start": start,
        "end": end,
        "step": 60,
    }
    data = _api_get(url, params=params)
    if "metrics" not in data:
        print(f"[WARN] No 'metrics' key in response for {metrics_type} on LB {lbid}")
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


if __name__ == '__main__':
    print('Hetzner Load Balancer Exporter is starting ...')

    if load_balancer_ids.lower() == 'all':
        load_balancer_ids_list = [lb['id'] for lb in get_all_load_balancers_ids()]
    else:
        load_balancer_ids_list = list(load_balancer_ids.split(","))

    print('Getting Info from Hetzner ...\n')
    print(f"Found Load Balancer{'s' if len(load_balancer_ids_list) > 1 else ''}")

    load_balancer_full_list = []
    for load_balancer_id in load_balancer_ids_list:
        load_balancer = get_load_balancer_info(load_balancer_id).get('load_balancer')
        if not load_balancer:
            print(f"[ERROR] Could not retrieve info for LB '{load_balancer_id}'. Skipping.")
            continue
        try:
            load_balancer_name = load_balancer['name']
        except KeyError as e:
            print("Couldn't get field", e)
            sys.exit(1)

        load_balancer_type = 'tcp'
        for service in load_balancer.get('services', []):
            if service.get('protocol') in ('http', 'https'):
                load_balancer_type = 'http'
                break

        load_balancer_full_list.append([load_balancer_id, load_balancer_name, load_balancer_type])
        print(f'\n\tName:\t{load_balancer_name}\n\tId:\t{load_balancer_id}\n\tType:\t{load_balancer_type}')

    if not load_balancer_full_list:
        print("[FATAL] No load balancers could be loaded. Exiting.")
        sys.exit(1)

    print(f'\nScrape interval: {SCRAPE_INTERVAL} seconds')

    print('\nBuilding server name cache from Hetzner for labeling ...')
    server_name_cache = get_all_server_names()
    print(f'Retrieved {len(server_name_cache)} server names from Hetzner ...\n')

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
    print('\nHetzner Load Balancer Exporter started')
    print('Visit http://localhost:8000/ to view the metrics')

    hetzner_load_balancer_info.info({'version': EXPORTER_VERSION, 'buildhost': 'wacken89'})

    while True:
        for load_balancer_id, lb_name, load_balancer_type in load_balancer_full_list:
            base_labels = {
                'hetzner_load_balancer_id': load_balancer_id,
                'hetzner_load_balancer_name': lb_name,
            }

            # Open connections + connections-per-second
            set_gauge(
                hetzner_openconnections, base_labels,
                extract_latest_metric_value(
                    get_metrics('open_connections', load_balancer_id),
                    'open_connections',
                ),
            )
            set_gauge(
                hetzner_connections_per_second, base_labels,
                extract_latest_metric_value(
                    get_metrics('connections_per_second', load_balancer_id),
                    'connections_per_second',
                ),
            )

            if load_balancer_type == 'http':
                set_gauge(
                    hetzner_requests_per_second, base_labels,
                    extract_latest_metric_value(
                        get_metrics('requests_per_second', load_balancer_id),
                        'requests_per_second',
                    ),
                )

            # Optimization: bandwidth in & out come from the same response —
            # fetch it once instead of twice per scrape cycle.
            bandwidth_payload = get_metrics('bandwidth', load_balancer_id)
            set_gauge(
                hetzner_bandwidth_in, base_labels,
                extract_latest_metric_value(bandwidth_payload, 'bandwidth.in'),
            )
            set_gauge(
                hetzner_bandwidth_out, base_labels,
                extract_latest_metric_value(bandwidth_payload, 'bandwidth.out'),
            )

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

        time.sleep(int(SCRAPE_INTERVAL))