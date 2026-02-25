import datetime
import time
import json
import os
import sys
from typing import Optional
from prometheus_client import start_http_server, Gauge, Info
import requests
from pathlib import Path


def load_env(var: str, default: Optional[any] = None) -> Optional[str]:
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
    exit(1)

HETZNER_CLOUD_API_URL_BASE = 'https://api.hetzner.cloud/v1'
HETZNER_CLOUD_API_URL_LB = f'{HETZNER_CLOUD_API_URL_BASE}/load_balancers/'
HETZNER_CLOUD_API_URL_SERVER = f'{HETZNER_CLOUD_API_URL_BASE}/servers/'


def get_all_load_balancers_ids() -> dict:
    url = f'{HETZNER_CLOUD_API_URL_LB}'
    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    get = requests.get(url, headers=headers)
    return get.json()['load_balancers']


def get_load_balancer_info(lbid) -> dict:
    url = f'{HETZNER_CLOUD_API_URL_LB}{lbid}'

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    get = requests.get(url, headers=headers)
    return get.json()


def get_all_server_names() -> dict:
    url = f'{HETZNER_CLOUD_API_URL_SERVER}'

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    get = requests.get(url, headers=headers)
    return {x['id']: x['name'] for x in get.json()['servers']}


def get_server_info(server_id) -> dict:
    url = f'{HETZNER_CLOUD_API_URL_SERVER}{server_id}'

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    get = requests.get(url, headers=headers)
    return get.json()


def get_server_name_from_cache(server_id: str) -> str:
    global server_name_cache
    if server_id in server_name_cache:
        return server_name_cache[server_id]
    else:
        # Refresh cache
        server_name_cache = get_all_server_names()
        # Check again
        if server_id in server_name_cache:
            return server_name_cache[server_id]
        else:
            # If still not found, return id as name
            return server_id


def get_metrics(metrics_type, lbid):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    start = (now - datetime.timedelta(hours=1)).isoformat()
    end = now.isoformat()

    url = f"{HETZNER_CLOUD_API_URL_LB}{lbid}/metrics"

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    params = {
        "type": metrics_type,
        "start": start,
        "end": end,
        "step": 60
    }

    get = requests.get(url, headers=headers, params=params)
    try:
        data = get.json()
        if "metrics" not in data:
            print(f"[WARN] No 'metrics' key in response for {metrics_type} on LB {lbid}")
            print("[DEBUG] Raw response:", data)
        return data
    except Exception as e:
        print(f"[ERROR] Failed to parse metrics response: {e}")
        print("[DEBUG] Raw response text:", get.text)
        return {}



if __name__ == '__main__':

    print('Hetzner Load Balancer Exporter is starting ...')

    if load_balancer_ids.lower() == 'all':
        load_balancer_ids_list = []
        for key in get_all_load_balancers_ids():
            load_balancer_ids_list.append(key['id'])
    else:
        load_balancer_ids_list = list(load_balancer_ids.split(","))

    print('Getting Info from Hetzner ...\n')
    print(f"Found Load Balancer{'s' if len(load_balancer_ids_list) > 1 else ''}")

    load_balancer_full_list = []

    for load_balancer_id in load_balancer_ids_list:
        load_balancer = get_load_balancer_info(load_balancer_id).get('load_balancer')
        try:
            load_balancer_name = load_balancer['name']
        except Exception as e:
            print("Couldn't get field", e )
            sys.exit(1)


        for services in load_balancer['services']:
            if services['protocol'] in ['http', 'https']:
                LOAD_BALANCER_TYPE = 'http'
            else:
                LOAD_BALANCER_TYPE = 'tcp'

        load_balancer_full_list.append([load_balancer_id, load_balancer_name, LOAD_BALANCER_TYPE])

        print(f'\n\tName:\t{load_balancer_name}\n\tId:\t{load_balancer_id}\n\tType:\t{LOAD_BALANCER_TYPE}')

    print(f'\nScrape interval: {SCRAPE_INTERVAL} seconds')

    print('\nBuilding server name cache from Hetzner for labeling ...')
    server_name_cache = get_all_server_names()
    print(f'Retrieved {len(server_name_cache.keys())} server names from Hetzner ...\n')

    id_name_list = ['hetzner_load_balancer_id', 'hetzner_load_balancer_name']
    hetzner_load_balancer_info = Info('hetzner_load_balancer', 'Hetzner Load Balancer Exporter build info')
    hetzner_openconnections = Gauge('hetzner_load_balancer_open_connections', 'Open Connections on Hetzner Load Balancer', id_name_list)
    hetzner_connections_per_second = Gauge('hetzner_load_balancer_connections_per_second', 'Connections per Second on Hetzner Load Balancer', id_name_list)
    hetzner_requests_per_second = Gauge('hetzner_load_balancer_requests_per_second', 'Requests per Second on Hetzner Load Balancer', id_name_list)
    hetzner_bandwidth_in = Gauge('hetzner_load_balancer_bandwidth_in', 'Bandwidth in on Hetzner Load Balancer', id_name_list)
    hetzner_bandwidth_out = Gauge('hetzner_load_balancer_bandwidth_out', 'Bandwidth out on Hetzner Load Balancer', id_name_list)
    id_name_service_list = id_name_list + ['hetzner_target_id', 'hetzner_target_name', 'hetzner_target_port']
    hetzner_service_state = Gauge('hetzner_load_balancer_service_state', 'Health status of Load Balancer\'s services', id_name_service_list)

    start_http_server(8000)
    print('\nHetzner Load Balancer Exporter started')
    print('Visit http://localhost:8000/ to view the metrics')
    hetzner_load_balancer_info.info({'version': '1.2.0', 'buildhost': 'drake0103@gmail.com'})

    while True:
        for load_balancer_id, lb_name, load_balancer_type in load_balancer_full_list:
            hetzner_openconnections.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('open_connections',load_balancer_id)["metrics"]["time_series"]["open_connections"]["values"][0][1])
            hetzner_connections_per_second.labels(hetzner_load_balancer_id=load_balancer_id,
                            hetzner_load_balancer_name=lb_name).set(get_metrics('connections_per_second',load_balancer_id)["metrics"]["time_series"]["connections_per_second"]["values"][0][1])
            if load_balancer_type == 'http':
                hetzner_requests_per_second.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('requests_per_second',load_balancer_id)["metrics"]["time_series"]["requests_per_second"]["values"][0][1])
            hetzner_bandwidth_in.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('bandwidth',load_balancer_id)["metrics"]["time_series"]["bandwidth.in"]["values"][0][1])
            hetzner_bandwidth_out.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('bandwidth',load_balancer_id)["metrics"]["time_series"]["bandwidth.out"]["values"][0][1])

            lb_info = get_load_balancer_info(load_balancer_id)['load_balancer']

            targets = []
            for x in lb_info['targets']:
                if x['type'] == 'server' or x['type'] == 'ip':
                    targets.append(x)
                elif x['type'] == 'label_selector':
                    targets.extend(x['targets'])

            for target in targets:
                for health_status in target['health_status']:
                    hetzner_service_state.labels(hetzner_load_balancer_id=load_balancer_id,
                                                 hetzner_load_balancer_name=lb_name,
                                                 hetzner_target_id=(target['ip']['ip'] if target['type'] == 'ip' else target['server']['id']),
                                                 hetzner_target_name=(target['ip']['ip'] if target['type'] == 'ip' else get_server_name_from_cache(target['server']['id'])),
                                                 hetzner_target_port=health_status['listen_port'])\
                        .set((1 if health_status['status'] == 'healthy' else 0))
        time.sleep(int(SCRAPE_INTERVAL))
