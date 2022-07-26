import datetime
import time
import json
import os
import sys
from prometheus_client import start_http_server, Gauge, Info
import requests


try:
    load_balancer_ids = os.environ['LOAD_BALANCER_IDS']
except KeyError:
    print('Variable LOAD_BALANCER_IDS not defined')
    sys.exit(1)

try:
    access_token = os.environ['ACCESS_TOKEN']
except KeyError:
    print('Variable ACCESS_TOKEN not defined')
    sys.exit(1)

try:
    SCRAPE_INTERVAL = os.environ['SCRAPE_INTERVAL']
except KeyError:
    print('Variable SCRAPE_INTERVAL not defined using default')
    SCRAPE_INTERVAL = 30


HETZNER_CLOUD_API_URL = 'https://api.hetzner.cloud/v1/load_balancers/'

def get_all_load_balancers_ids():
    url = f'{HETZNER_CLOUD_API_URL}'
    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    get = requests.get(url, headers=headers)
    return get.json()['load_balancers']


def get_load_balancer_info(lbid):
    url = f'{HETZNER_CLOUD_API_URL}{lbid}'

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    get = requests.get(url, headers=headers)
    return get.json()


def get_metrics(metrics_type, lbid):
    utc_offset_sec = time.altzone if time.localtime().tm_isdst else time.timezone
    utc_offset = datetime.timedelta(seconds=-utc_offset_sec)
    hetzner_date = datetime.datetime.now().replace(tzinfo=datetime.timezone(offset=utc_offset)).isoformat()

    url = f"{HETZNER_CLOUD_API_URL}{lbid}/metrics"

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {access_token}"
    }

    data = {
        "type": metrics_type,
        "start": hetzner_date,
        "end": hetzner_date,
        "step": 60
    }

    get = requests.get(url, headers=headers, data=json.dumps(data))
    return get.json()


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
            print('Couldnt get field', e )
            sys.exit(1)


        for services in load_balancer['services']:
            if services['protocol'] == 'http':
                LOAD_BALANCER_TYPE = 'http'
            else:
                LOAD_BALANCER_TYPE = 'tcp'

        load_balancer_full_list.append([load_balancer_id, load_balancer_name, LOAD_BALANCER_TYPE])

        print(f'\n\tName:\t{load_balancer_name}\n\tId:\t{load_balancer_id}\n\tType:\t{LOAD_BALANCER_TYPE}')

    print(f'\nScrape intreval: {SCRAPE_INTERVAL} seconds')

    id_name_list = ['hetzner_load_balancer_id', 'hetzner_load_balancer_name']
    hetzner_load_balancer_info = Info('hetzner_load_balancer', 'Hetzner Load Balancer Exporter build info')
    hetzner_openconnections = Gauge('hetzner_load_balancer_open_connections', 'Open Connections on Hetzner Load Balancer', id_name_list)
    hetzner_connections_per_second = Gauge('hetzner_load_balancer_connections_per_second', 'Connections per Second on Hetzner Load Balancer', id_name_list)
    hetzner_requests_per_second = Gauge('hetzner_load_balancer_requests_per_second', 'Requests per Second on Hetzner Load Balancer', id_name_list)
    hetzner_bandwidth_in = Gauge('hetzner_load_balancer_bandwidth_in', 'Bandwidth in on Hetzner Load Balancer', id_name_list)
    hetzner_bandwidth_out = Gauge('hetzner_load_balancer_bandwidth_out', 'Bandwidth out on Hetzner Load Balancer', id_name_list)

    start_http_server(8000)
    print('\nHetzner Load Balancer Exporter started')
    print('Visit http://localhost:8000/ to view the metrics')
    hetzner_load_balancer_info.info({'version': '2.0.0', 'buildhost': 'drake0103@gmail.com'})

    while True:
        for load_balancer_id, lb_name, load_balancer_type in load_balancer_full_list:
            hetzner_openconnections.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('open_connections',load_balancer_id)["metrics"]["time_series"]["open_connections"]["values"][0][1])
            hetzner_connections_per_second.labels(hetzner_load_balancer_id=load_balancer_id,
                            hetzner_load_balancer_name=lb_name).set(get_metrics('connections_per_second',load_balancer_id)["metrics"]["time_series"]["connections_per_second"]["values"][0][1])
            if load_balancer_type == 'http':
                hetzner_connections_per_second.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('requests_per_second',load_balancer_id)["metrics"]["time_series"]["requests_per_second"]["values"][0][1])
            hetzner_bandwidth_in.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('bandwidth',load_balancer_id)["metrics"]["time_series"]["bandwidth.in"]["values"][0][1])
            hetzner_bandwidth_out.labels(hetzner_load_balancer_id=load_balancer_id,
                                hetzner_load_balancer_name=lb_name).set(get_metrics('bandwidth',load_balancer_id)["metrics"]["time_series"]["bandwidth.out"]["values"][0][1])
        time.sleep(int(SCRAPE_INTERVAL))