from prometheus_client import start_http_server, Summary, Gauge
import datetime, time
import requests
import json
import os
import sys

try:
  loadBalancerId = os.environ['LOAD_BALANCER_ID']
except KeyError:
  print('Variable LOAD_BALANCER_ID not defined')
  sys.exit(1)


try:
  accessToken = os.environ['ACCEESS_TOKEN']
except KeyError:
  print('Variable ACCEESS_TOKEN not defined')
  sys.exit(1)


requestUrl = 'https://api.hetzner.cloud/v1/load_balancers/' + loadBalancerId



def getLoadBalancerType(id):
    url = requestUrl
    
    headers = {
        'Content-type': "application/json",
        'Authorization': "Bearer " +  accessToken
    }

    get = requests.get(url, headers=headers)
    return get.json()


def getMetrics(metricsType):
    utc_offset_sec = time.altzone if time.localtime().tm_isdst else time.timezone
    utc_offset = datetime.timedelta(seconds=-utc_offset_sec)
    hetznerDate = datetime.datetime.now().replace(tzinfo=datetime.timezone(offset=utc_offset)).isoformat()

    url = requestUrl + '/metrics'

    headers = {
        'Content-type': "application/json",
        'Authorization': "Bearer " +  accessToken
    }

    data = {
        "type": metricsType,
        "start": hetznerDate,
        "end": hetznerDate,
        "step": 60
    }

    get = requests.get(url, headers=headers, data=json.dumps(data))
    return get.json()


if __name__ == '__main__':

  for services in getLoadBalancerType(loadBalancerId)['load_balancer']['services']:
      if services['protocol'] == 'http':
          lbType = 'http'      
      else:
          lbType = 'tcp'

  HetznerOpenConnections = Gauge('hetzner_load_balancer_open_connections', 'Open Connections on Hetzner Load Balancer')
  HetznerConnectionsPerSecond = Gauge('hetzner_load_balancer_connections_per_second', 'Connections per Second on Hetzner Load Balancer')
  HetznerRequestsPerSecond = Gauge('hetzner_load_balancer_requests_per_second', 'Requests per Second on Hetzner Load Balancer')
  HetznerBandwidthIn = Gauge('hetzner_load_balancer_bandwidth_in', 'Bandwidth in on Hetzner Load Balancer')
  HetznerBandwidthOut = Gauge('hetzner_load_balancer_bandwidth_out', 'Bandwidth out on Hetzner Load Balancer')
  start_http_server(8000)
  

  while True:
    HetznerOpenConnections.set_function(lambda: getMetrics('open_connections')["metrics"]["time_series"]["open_connections"]["values"][0][1])
    HetznerConnectionsPerSecond.set_function(lambda: getMetrics('connections_per_second')["metrics"]["time_series"]["connections_per_second"]["values"][0][1])
    if lbType == 'http':
      HetznerConnectionsPerSecond.set_function(lambda: getMetrics('requests_per_second')["metrics"]["time_series"]["requests_per_second"]["values"][0][1])
    HetznerBandwidthIn.set_function(lambda: getMetrics('bandwidth')["metrics"]["time_series"]["bandwidth.in"]["values"][0][1])
    HetznerBandwidthOut.set_function(lambda: getMetrics('bandwidth')["metrics"]["time_series"]["bandwidth.out"]["values"][0][1])
    time.sleep(1)