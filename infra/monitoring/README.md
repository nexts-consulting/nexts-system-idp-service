# IDP Monitoring Stack

## Components

- **Prometheus** — scrapes `/metrics` from all IDP services
- **Grafana** — dashboards under `grafana/dashboards/`
- **Alertmanager** — routes alerts from `prometheus/rules/`
- **OTel Collector** — receives OTLP from services (gRPC 4317)
- **Pushgateway** — for RunPod ephemeral GPU metrics push

## RunPod metrics push

From the extraction pod:

```bash
echo "idp_extraction_tokens_per_second $(cat value)" | curl --data-binary @- http://PUSHGATEWAY:9091/metrics/job/idp-extraction
```

## Baseline comparison

See `docs/performance/baseline.json`. Alert fires when throughput &lt; 85% of 472.84 tok/s.
