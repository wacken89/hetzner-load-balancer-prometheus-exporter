# Hetzner Load Balancer Prometheus Exporter

Exports metrics from Hetzner Load Balancer for consumption by Prometheus

## Preparing

### API TOKEN

Go to [Hetzner Console](console.hetzner.cloud). Open project where you have running Load Balancer and create `API TOKEN` in Security section

![api token](img/api_token.png "API TOKEN")

### Load Balancer ID

Next we sholud get `ID` of our Load Balancer. This information we will get from `Hetzner API`, everything about `API` you find in [official API documentation](https://docs.hetzner.cloud/#load-balancers-get-all-load-balancers)

Example `curl`

```bash
curl \
    -H "Authorization: Bearer $API_TOKEN" \
	'https://api.hetzner.cloud/v1/load_balancers'
```

Response sample

```json
{
  "load_balancers": [
    {
      "id": 4711,
      "name": "Web Frontend",
      "public_net": {
        "enabled": false,
        "ipv4": {
          "ip": "1.2.3.4"
        },
...
    }
}
```

### Configuring

The exporter can be configured using environment variables. Instead of providing the values directly, you can also use the variables suffixed with `_FILE` to provide a file containing the value, which is useful with mounted secrets, for example.

| Enviroment  | Description | 
| ------- | ------ |
| `LOAD_BALANCER_IDS` | Supported string with specific id `11,22,33` or `all` for scraping metrics from all load balancers in the project |
| `LOAD_BALANCER_IDS_PATH` | Path to a file containing the load balancer IDs |
| `ACCESS_TOKEN` | Hetzner API token |
| `ACCESS_TOKEN_PATH` | Path to a file containing the Hetzner API token |
| Optional `SCRAPE_INTERVAL` | value in seconds, default value is `30 seconds` |
| Optional `SCRAPE_INTERVAL_PATH` | Path to a file containing the scrape interval |

#### Kubernetes usage

In `deploy/kubernetes.yaml` add in `env` section id which we got from `API` and `API TOKEN`

```yaml
env:
  - name: LOAD_BALANCER_IDS
    value: "11,22,33,44"
  - name: ACCESS_TOKEN
    value: "ewsfds43r*****132"
  ## Optional
  - name: SCRAPE_INTERVAL
    value: '60'
```

Deploy it to Kubernetes cluster

```bash
kubectl apply -f deploy/kubernetes.yaml
```

Or use [helm](https://helm.sh/docs/) to deploy the exporter:  
In `deploy/helm-chart/values.yaml` add in `env` section id which we got from `API` and `API TOKEN`

```bash
# Add repo
helm repo add wacken89 https://wacken89.github.io/hetzner-load-balancer-prometheus-exporter
helm repo update
# Install chart
helm install hcloud-lb-exporter wacken89/hetzner-load-balancer-exporter -f values.yml
```

### Check metrics page

```bash
kubectl port-forward <pod> 8000:8000
```

Open in your browser `localhost:8000`:

![exporter metrics](img/exporter_metrics.png)


## Grafana

Grafana Dashboard you can find [here](example/grafana-dashboard/hetzner-load-balancer.json)

Metrics in Hetzner console
![Hetzner console](img/hetzner_lb_metrics.png)

Metrics in Grafana
![exporter metrics](img/grafana_metrics.png)
