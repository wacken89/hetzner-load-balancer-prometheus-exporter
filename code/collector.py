from prometheus_client import start_http_server, Summary, Gauge, Info
import datetime, time
import requests
import json
import os
import sys

try:
  loadBalancerIds = os.environ['LOAD_BALANCER_IDS']
except KeyError:
  print('Variable LOAD_BALANCER_ID not defined')
  sys.exit(1)

try:
  accessToken = os.environ['ACCESS_TOKEN']
except KeyError:
  print('Variable ACCESS_TOKEN not defined')
  sys.exit(1)


hetznerCloudApiUrl = 'https://api.hetzner.cloud/v1/load_balancers/'

def getLoadBalancerType(id):
    url = f'{hetznerCloudApiUrl}{id}'

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {accessToken}"
    }

    get = requests.get(url, headers=headers)
    return get.json()


def getMetrics(metricsType, id):
    utc_offset_sec = time.altzone if time.localtime().tm_isdst else time.timezone
    utc_offset = datetime.timedelta(seconds=-utc_offset_sec)
    hetznerDate = datetime.datetime.now().replace(tzinfo=datetime.timezone(offset=utc_offset)).isoformat()

    url = f"{hetznerCloudApiUrl}{id}/metrics"

    headers = {
        'Content-type': "application/json",
        'Authorization': f"Bearer {accessToken}"
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

  print('Hetzner Load Balancer Exporter is starting ...')

  loadBalanceridsList = list(loadBalancerIds.split(","))

  print('Getting Info from Hetzner ...\n')

  print("Found Load Balancer{'s' if len(loadBalanceridsList > 1)}
#   if len(loadBalanceridsList) <= 1:
#     print('Found Load Balancer:')
#   else:
#     print('Found Load Balancers:')

  loadBalancerFullList = []

  for loadBalancerId in loadBalanceridsList:
    loadBalancer = getLoadBalancerType(loadBalancerId).get('load_balancer')
    try:
      loadBalancerName = loadBalancer['name']
    except Exception as e:
      print('Couldnt get field', e )
      sys.exit(1)


    for services in getLoadBalancer['services']:
        if services['protocol'] == 'http':
            lbType = 'http'
        else:
            lbType = 'tcp'
    
    loadBalancerFullList.append([loadBalancerId, loadBalancerName, lbType])

    print(f'\n\tName:\t {loadBalancerName}\n\tId:\t{loadBalancerId}\n\tType:\t{lbType})

  id_name_list = ['hetzner_load_balancer_id', 'hetzner_load_balancer_name']
  HetznerLoadBalancerInfo = Info('hetzner_load_balancer', 'Hetzner Load Balancer Exporter build info')
  HetznerOpenConnections = Gauge('hetzner_load_balancer_open_connections', 'Open Connections on Hetzner Load Balancer', id_name_list)
  HetznerConnectionsPerSecond = Gauge('hetzner_load_balancer_connections_per_second', 'Connections per Second on Hetzner Load Balancer', id_name_list)
  HetznerRequestsPerSecond = Gauge('hetzner_load_balancer_requests_per_second', 'Requests per Second on Hetzner Load Balancer', id_name_list)
  HetznerBandwidthIn = Gauge('hetzner_load_balancer_bandwidth_in', 'Bandwidth in on Hetzner Load Balancer', id_name_list)
  HetznerBandwidthOut = Gauge('hetzner_load_balancer_bandwidth_out', 'Bandwidth out on Hetzner Load Balancer', id_name_list)

  start_http_server(8000)
  print('Hetzner Load Balancer Exporter started on port 8000\n')
  HetznerLoadBalancerInfo.info({'version': '2.0.0', 'buildhost': 'drake0103@gmail.com'})

  while True:
#     for loadBalanacerFullInfo in loadBalancerFullList:
      for lb_id, lb_name, lb_type in loadBalancerFullList:
      try:
        # HetznerOpenConnections.labels(hetzner_load_balancer_id=loadBalanacerFullInfo[0], hetzner_load_balancer_name=loadBalanacerFullInfo[1]).set_function(lambda: getMetrics('open_connections',loadBalanacerFullInfo[0])["metrics"]["time_series"]["open_connections"]["values"][0][1])
        HetznerOpenConnections.labels(hetzner_load_balancer_id=lb_id,
                                      hetzner_load_balancer_name=lb_name).set(getMetrics('open_connections',lb_id)["metrics"]["time_series"]["open_connections"]["values"][0][1])
      except:
        print('Couldnt get field', e )
#       HetznerConnectionsPerSecond.labels(hetzner_load_balancer_id=lb_id, hetzner_load_balancer_name=lb_name).set_function(
#         getMetrics('connections_per_second',loadBalanacerFullInfo[0])["metrics"]["time_series"]["connections_per_second"]["values"][0][1])
      #HetznerConnectionsPerSecond.labels(hetzner_load_balancer_id=lb_id,
#                                         hetzner_load_balancer_name=lb_name).set(getMetrics('connections_per_second',lb_id)["metrics"]["time_series"]["connections_per_second"]["values"][0][1])
      if lb_type == 'http':
        HetznerConnectionsPerSecond.labels(hetzner_load_balancer_id=lb_id, hetzner_load_balancer_name=lb_name).set(getMetrics('requests_per_second',lb_id)["metrics"]["time_series"]["requests_per_second"]["values"][0][1])
      HetznerBandwidthIn.labels(hetzner_load_balancer_id=lb_id, hetzner_load_balancer_name=lb_name).set(getMetrics('bandwidth',lb_id)["metrics"]["time_series"]["bandwidth.in"]["values"][0][1])
      HetznerBandwidthOut.labels(hetzner_load_balancer_id=lb_id, hetzner_load_balancer_name=lb_name).set(getMetrics('bandwidth',lb_id)["metrics"]["time_series"]["bandwidth.out"]["values"][0][1])

    time.sleep(15)
