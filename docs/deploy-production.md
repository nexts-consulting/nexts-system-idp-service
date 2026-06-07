# Hướng dẫn triển khai Production — Nexts IDP Service

Tài liệu này mô tả cách triển khai hệ thống trích xuất hóa đơn (IDP) lên môi trường production theo kiến trúc đã implement trong repo.

**Kiến trúc production (tóm tắt):**


| Thành phần                                      | Nơi chạy                          | Ghi chú              |
| ----------------------------------------------- | --------------------------------- | -------------------- |
| `idp-app`, `idp-orchestrator`, `idp-preprocess` | GCP (VM + Docker)                 | Luôn bật             |
| PostgreSQL, Redis, GCS                          | GCP (Cloud SQL, Memorystore, GCS) | Managed              |
| `idp-extraction` + lmdeploy + InternVL          | **RunPod** (GPU)                  | Có thể tắt khi idle  |
| Monitoring                                      | GCP VM hoặc cùng stack Docker     | Prometheus + Grafana |


Tham khảo thêm: [architecture.md](architecture.md), [infrastructure.md](infrastructure.md), [runbooks/](runbooks/), [performance/baseline.json](performance/baseline.json).

---

## Mục lục

1. [Điều kiện tiên quyết](#1-điều-kiện-tiên-quyết)
2. [Thứ tự triển khai](#2-thứ-tự-triển-khai)
3. [GCP — Infrastructure (Terraform)](#3-gcp--infrastructure-terraform)
4. [Cấu hình secrets và biến môi trường](#4-cấu-hình-secrets-và-biến-môi-trường)
5. [Triển khai services trên GCP VM](#5-triển-khai-services-trên-gcp-vm)
6. [RunPod — GPU extraction](#6-runpod--gpu-extraction)
7. [Kết nối Orchestrator ↔ RunPod](#7-kết-nối-orchestrator--runpod)
8. [Model và checkpoint](#8-model-và-checkpoint)
9. [Monitoring và cảnh báo](#9-monitoring-và-cảnh-báo)
10. [Kiểm tra sau deploy](#10-kiểm-tra-sau-deploy)
11. [Vận hành hàng ngày](#11-vận-hành-hàng-ngày)
12. [Rollback và khắc phục sự cố](#12-rollback-và-khắc-phục-sự-cố)

---

## 1. Điều kiện tiên quyết

### Tài khoản và công cụ

- GCP project với quyền tạo VPC, Cloud SQL, Memorystore, GCS, Compute Engine, Secret Manager
- [Terraform](https://www.terraform.io/) >= 1.5, [Google provider](https://registry.terraform.io/providers/hashicorp/google/latest) ~> 5.30
- Docker và Docker Compose trên VM deploy
- Tài khoản RunPod + API key
- Checkpoint DocTamper: `mit_unet_doctamper_best.pt` (upload lên GCS hoặc mount trên preprocess VM)

### Artifact cần chuẩn bị


| Artifact          | Đường dẫn gợi ý                                   | Dùng cho         |
| ----------------- | ------------------------------------------------- | ---------------- |
| DocTamper weights | `gs://<bucket>/models/mit_unet_doctamper_best.pt` | `idp-preprocess` |
| Docker images     | GCR / Artifact Registry                           | 4 services       |
| InternVL model    | HuggingFace trên RunPod volume                    | lmdeploy         |


---

## 2. Thứ tự triển khai

```text
1. Terraform apply (GCP: network, GCS, Cloud SQL, Redis, IAM, VMs, secrets)
2. Chạy migration DB + seed prompts
3. Upload DocTamper checkpoint → GCS
4. Deploy monitoring stack (Prometheus/Grafana) trên monitoring VM
5. Deploy idp-app, idp-orchestrator, idp-preprocess trên app VM (Docker Compose production)
6. Tạo RunPod pod (lmdeploy + idp-extraction)
7. Cấu hình EXTRACTION_URL trên orchestrator → URL public RunPod
8. Warm RunPod + smoke test production
9. Bật alert và backup DB
```

---

## 3. GCP — Infrastructure (Terraform)

### 3.1 Cấu hình biến

```bash
cd deploy/terraform/gcp
cp terraform.tfvars.example terraform.tfvars
```

Chỉnh `terraform.tfvars`:

```hcl
project_id = "your-gcp-project-id"
region     = "asia-southeast1"   # gần VN
env        = "prod"
db_tier    = "db-custom-2-4096"  # prod: tăng tier
redis_memory_gb = 2
vm_machine_type = "e2-standard-4"
```

### 3.2 Apply

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### 3.3 Output quan trọng

Sau `apply`, lưu các giá trị:

```bash
terraform output artifacts_bucket
terraform output redis_host
terraform output app_vm_ip
terraform output monitoring_vm_ip
terraform output app_service_account
terraform output -raw cloud_sql_connection   # sensitive
```


| Output                 | Dùng cho                                  |
| ---------------------- | ----------------------------------------- |
| `artifacts_bucket`     | `GCS_BUCKET` trên preprocess / extraction |
| `redis_host`           | `REDIS_URL=redis://<host>:6379/0`         |
| `cloud_sql_connection` | Cloud SQL Auth Proxy hoặc private IP      |
| `app_vm_ip`            | SSH, deploy Docker, (tạm) public API      |
| `app_service_account`  | Gắn vào VM, quyền GCS/Secret              |


### 3.4 Modules đã có trong repo


| Module        | Path                    | Tạo ra                                 |
| ------------- | ----------------------- | -------------------------------------- |
| network       | `modules/network`       | VPC, subnet `10.0.0.0/24`              |
| storage       | `modules/storage`       | Bucket artifacts, lifecycle 90 ngày    |
| database      | `modules/database`      | Cloud SQL Postgres 16                  |
| cache         | `modules/cache`         | Memorystore Redis 7                    |
| iam           | `modules/iam`           | SA cho app / preprocess / orchestrator |
| compute       | `modules/compute`       | VM chạy Docker Compose                 |
| secrets       | `modules/secrets`       | Secret Manager placeholders            |
| observability | `modules/observability` | VM monitoring + uptime/alert mẫu       |


**Lưu ý bảo mật production:**

- Đổi mật khẩu DB trong Terraform / dùng Secret Manager (placeholder hiện tại: `CHANGE_ME_USE_SECRET_MANAGER`)
- Hạn chế firewall: chỉ mở port cần thiết; API public nên qua Load Balancer + TLS
- Cloud SQL: bật private IP + Cloud SQL Auth Proxy thay vì public IP
- **SSH vào VM:** Terraform mở port `22` chỉ cho IAP (`35.235.240.0/20`), không mở SSH public. Sau `terraform apply`:

```bash
gcloud compute ssh idp-dev-app-vm --zone=asia-southeast1-a --tunnel-through-iap
gcloud compute scp deploy/docker/.env.prod idp-dev-app-vm:/tmp/ --zone=asia-southeast1-a --tunnel-through-iap
```

Cần quyền `roles/iap.tunnelResourceAccessor` trên VM (hoặc `roles/compute.instanceAdmin.v1`).

---

## 4. Cấu hình secrets và biến môi trường

### 4.0 File env đã generate (sau Terraform bước 3)

Repo đã có sẵn file env production lấy từ Terraform output (`env=dev`):

| File | Mục đích |
| ---- | -------- |
| [deploy/docker/.env.prod](../deploy/docker/.env.prod) | Biến thật cho VM (**không commit**) |
| [deploy/docker/.env.prod.example](../deploy/docker/.env.prod.example) | Template an toàn để commit |
| [deploy/docker/docker-compose.prod.yml](../deploy/docker/docker-compose.prod.yml) | Compose GCP: Cloud SQL proxy + app / orchestrator / preprocess |
| [deploy/gcp/setup-vm-secrets.sh](../deploy/gcp/setup-vm-secrets.sh) | Tạo SA key, IAM Cloud SQL, sync Secret Manager |
| [deploy/gcp/render-env-from-terraform.sh](../deploy/gcp/render-env-from-terraform.sh) | Tái tạo `.env.prod` sau khi `terraform apply` |
| [deploy/gcp/deploy-on-app-vm.sh](../deploy/gcp/deploy-on-app-vm.sh) | Script deploy trên app VM |
| [deploy/runpod/runpod.env.example](../deploy/runpod/runpod.env.example) | Tham chiếu RunPod pod ID / URL |

**Giá trị hiện tại (Terraform `dev` + RunPod):**

| Biến | Giá trị |
| ---- | ------- |
| `GCS_BUCKET` | `idp-dev-artifacts` |
| `REDIS_URL` | `redis://10.137.178.83:6379/0` |
| `CLOUD_SQL_CONNECTION` | `nexts-system-idp-service:asia-southeast1:idp-dev-pg` |
| `DATABASE_URL` | qua sidecar `cloud-sql-proxy:5432` |
| `RUNPOD_POD_ID` | `xc1fco8h90o2wx` |
| `EXTRACTION_URL` | `https://xc1fco8h90o2wx-8003.proxy.runpod.net` |
| `MOCK_EXTRACTION` | `false` |
| `APP_VM_IP` | `34.21.226.104` |

Trên **app VM**, deploy:

```bash
# Laptop: secrets + copy .env.prod (SSH qua IAP — không dùng user@IP trực tiếp)
bash deploy/gcp/setup-vm-secrets.sh
gcloud compute scp deploy/gcp/keys/preprocess-sa.json deploy/docker/.env.prod \
  idp-dev-app-vm:/tmp/ --zone=asia-southeast1-a --tunnel-through-iap
gcloud compute ssh idp-dev-app-vm --zone=asia-southeast1-a --tunnel-through-iap \
  --command='sudo mkdir -p /opt/idp/secrets && sudo mv /tmp/preprocess-sa.json /opt/idp/secrets/'

# VM: clone repo, copy .env.prod vào deploy/docker/, rồi:
bash deploy/gcp/deploy-on-app-vm.sh

# Warm RunPod sau deploy
curl -X POST "http://127.0.0.1:8001/internal/runpod/warm?warm=true"
```

Điền `FIREBASE_ALLOWED_BUCKETS` trong `.env.prod` nếu ảnh đầu vào từ Firebase Storage.

**Redis timeout từ app VM:** Memorystore phải dùng `authorized_network = idp-dev-vpc` (đã sửa trong `modules/cache`). Nếu Redis tạo trước đó không gắn VPC, chạy `terraform apply` (có thể **recreate** Redis → IP mới), rồi:

```bash
bash deploy/gcp/render-env-from-terraform.sh
# copy .env.prod lên VM, restart compose
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --force-recreate
```

Kiểm tra từ VM: `python3 -c "import socket; s=socket.socket(); s.settimeout(3); print('OK' if s.connect_ex(('$REDIS_HOST',6379))==0 else 'FAIL')"`

---

Lưu secrets trong **GCP Secret Manager** (đã tạo shell qua module `secrets`):


| Secret ID (pattern)             | Nội dung                                       |
| ------------------------------- | ---------------------------------------------- |
| `idp-prod-database-url`         | `postgresql+asyncpg://user:pass@host:5432/idp` |
| `idp-prod-runpod-api-key`       | RunPod API key (nếu tự động start pod)         |
| `idp-prod-callback-hmac-secret` | Chuỗi random cho ký webhook khách hàng         |


### 4.1 `idp-app` (port 8000)


| Biến                     | Production                                             |
| ------------------------ | ------------------------------------------------------ |
| `DATABASE_URL`           | Cloud SQL (qua proxy)                                  |
| `ORCHESTRATOR_URL`       | `http://idp-orchestrator:8001` (Docker network nội bộ) |
| `CALLBACK_HMAC_SECRET`   | Từ Secret Manager                                      |
| `ENV`                    | `prod`                                                 |
| `OTEL_EXPORTER_ENDPOINT` | OTel collector (optional)                              |


File mẫu: [services/idp-app/.env.example](../services/idp-app/.env.example)

### 4.2 `idp-orchestrator` (port 8001)


| Biến                        | Production                                                        |
| --------------------------- | ----------------------------------------------------------------- |
| `REDIS_URL`                 | `redis://<memorystore-host>:6379/0`                               |
| `EXTRACTION_URL`            | `**https://<runpod-id>-8003.proxy.runpod.net`** hoặc IP allowlist |
| `APP_INTERNAL_URL`          | `http://idp-app:8000`                                             |
| `BATCH_MAX_SIZE`            | `48` (theo benchmark InternVL A100)                               |
| `BATCH_MAX_WAIT_MS`         | `300`–`800`                                                       |
| `CIRCUIT_FAILURE_THRESHOLD` | `5`                                                               |
| `CIRCUIT_OPEN_SECONDS`      | `30`                                                              |


File mẫu: [services/idp-orchestrator/.env.example](../services/idp-orchestrator/.env.example)

### 4.3 `idp-preprocess` (port 8002)


| Biến                             | Production                                                               |
| -------------------------------- | ------------------------------------------------------------------------ |
| `REDIS_URL`                      | Cùng Redis                                                               |
| `GCS_BUCKET`                     | `idp-prod-artifacts` (từ terraform output) — **artifact** sau preprocess |
| `GCS_EMULATOR_HOST`              | **Để trống** (dùng GCS thật)                                             |
| `FIREBASE_PROJECT_ID`            | Firebase/GCP project ID                                                  |
| `FIREBASE_ALLOWED_BUCKETS`       | Bucket chứa ảnh hóa đơn đầu vào (comma-separated)                        |
| `ALLOWED_IMAGE_HOSTS`            | `firebasestorage.googleapis.com,...`                                     |
| `GOOGLE_APPLICATION_CREDENTIALS` | SA có `storage.objectViewer` trên bucket ảnh đầu vào                     |
| `DOCTAMPER_CHECKPOINT`           | `/models/mit_unet_doctamper_best.pt` (mount hoặc tải lúc start)          |
| `FRAUD_ENABLED`                  | `true`                                                                   |
| `TAMPER_PIXEL_THRESHOLD`         | `0.0001` (tune theo dataset)                                             |
| `MAX_IMAGE_EDGE`                 | `4096`                                                                   |


Chi tiết định dạng `image_urls`: [image-sources.md](image-sources.md).

**GPU preprocess (tùy chọn):** T4/L4 đủ cho MiT-UNet; có thể chạy CPU nếu chấp nhận latency cao hơn.

File mẫu: [services/idp-preprocess/.env.example](../services/idp-preprocess/.env.example)

### 4.4 `idp-extraction` trên RunPod (port 8003)


| Biến              | Production                                     |
| ----------------- | ---------------------------------------------- |
| `MOCK_MODE`       | `**false`**                                    |
| `LMDEPLOY_URL`    | `http://127.0.0.1:23333` (cùng pod)            |
| `MODEL_NAME`      | `OpenGVLab/InternVL3_5-8B-Flash`               |
| `GCS_BUCKET`      | Cùng bucket artifacts                          |
| `PUSHGATEWAY_URL` | URL Pushgateway trên GCP (metrics khi pod tắt) |


File config: [services/idp-extraction/src/idp_extraction/config.py](../services/idp-extraction/src/idp_extraction/config.py)

---

## 5. Triển khai services trên GCP VM

### 5.1 Database migration

Kết nối Cloud SQL và chạy:

```bash
psql "$DATABASE_URL" -f migrations/001_initial_schema.sql
psql "$DATABASE_URL" -f scripts/seed_prompts.sql
```

Hoặc mount migration vào lần init đầu (chỉ khi DB trống).

### 5.2 Build và push images

```bash
# Ví dụ Artifact Registry
export REGISTRY=asia-southeast1-docker.pkg.dev/PROJECT/idp
docker build -f services/idp-app/Dockerfile -t $REGISTRY/idp-app:prod .
docker build -f services/idp-orchestrator/Dockerfile -t $REGISTRY/idp-orchestrator:prod .
docker build -f services/idp-preprocess/Dockerfile -t $REGISTRY/idp-preprocess:prod .
docker build -f services/idp-extraction/Dockerfile -t $REGISTRY/idp-extraction:prod .

docker push $REGISTRY/idp-app:prod
# ... tương tự
```

### 5.3 Docker Compose production trên VM

Tạo file `deploy/docker/docker-compose.prod.yml` (hoặc override) trên VM với:

- Image từ registry thay vì `build`
- Không chạy `minio` — dùng GCS
- Không chạy `idp-extraction` — extraction trên RunPod
- Chỉ: `idp-app`, `idp-orchestrator`, `idp-preprocess` + network nội bộ

Tham khảo base: [deploy/docker/docker-compose.yml](../deploy/docker/docker-compose.yml).

Ví dụ override orchestrator:

```yaml
idp-orchestrator:
  environment:
    EXTRACTION_URL: https://YOUR_RUNPOD_HOST/v1/batch/extract  # base URL đến service :8003
    BATCH_MAX_SIZE: "48"
    REDIS_URL: redis://REDIS_IP:6379/0
```

**Lưu ý:** `EXTRACTION_URL` phải trỏ tới **root của idp-extraction** (path `/v1/batch/extract` được append trong code).

### 5.4 DocTamper trên preprocess VM

```bash
# Tải checkpoint từ GCS khi container start
gsutil cp gs://idp-prod-artifacts/models/mit_unet_doctamper_best.pt /models/
```

Mount volume vào container: `-v /models:/models`.

### 5.5 IAM

VM app gắn service account:

- **preprocess SA:** `roles/storage.objectAdmin` trên bucket artifacts
- **app SA:** quyền Cloud SQL Client + Secret Manager Accessor
- **orchestrator SA:** đọc GCS (nếu cần), gọi HTTPS ra RunPod

Chi tiết IAM: [deploy/terraform/gcp/modules/iam/main.tf](../deploy/terraform/gcp/modules/iam/main.tf).

---

## 6. RunPod — GPU extraction

### 6.1 Pod template

Dùng template có sẵn: [deploy/runpod/pod-template.json](../deploy/runpod/pod-template.json)

- GPU khuyến nghị: **NVIDIA A100 80GB** (batch 48, ~472 tok/s — xem [baseline.json](performance/baseline.json))
- Model: `OpenGVLab/InternVL3_5-8B-Flash`
- Port: `23333` (lmdeploy), `8003` (idp-extraction API)

### 6.2 Khởi động lmdeploy

Script: [deploy/runpod/start_lmdeploy.sh](../deploy/runpod/start_lmdeploy.sh)

```bash
export MODEL=OpenGVLab/InternVL3_5-8B-Flash
export PORT=23333
bash deploy/runpod/start_lmdeploy.sh
```

Chờ model load xong:

```bash
curl http://localhost:23333/v1/models
```

### 6.3 Chạy `idp-extraction` trên cùng pod

```bash
export MOCK_MODE=false
export LMDEPLOY_URL=http://127.0.0.1:23333
export GCS_BUCKET=idp-prod-artifacts
# Workload identity hoặc GOOGLE_APPLICATION_CREDENTIALS cho GCS
uvicorn idp_extraction.main:app --host 0.0.0.0 --port 8003
```

### 6.4 Bảo mật mạng

- Chỉ cho phép IP egress của **GCP orchestrator** gọi RunPod (RunPod firewall / Cloudflare tunnel)
- Không public lmdeploy `:23333` ra internet nếu không cần debug

### 6.5 Scale-to-zero (tiết kiệm chi phí)

- Dừng pod RunPod khi không có traffic (ban đêm / cuối tuần)
- Orchestrator giữ job trong Redis; khi pod cold → latency tăng, circuit breaker có thể OPEN
- Runbook: [runbooks/runpod-cold-start.md](runbooks/runpod-cold-start.md)

---

## 7. Kết nối Orchestrator ↔ RunPod

```text
[idp-orchestrator @ GCP]
    POST {EXTRACTION_URL}/v1/batch/extract
         → [idp-extraction @ RunPod :8003]
              → đọc ảnh từ GCS (gs://...)
              → POST {LMDEPLOY_URL}/v1/chat/completions
              → validate JSON schema
    POST {APP_INTERNAL_URL}/internal/v1/jobs/complete
         → [idp-app @ GCP]
```

Sau khi RunPod sẵn sàng:

```bash
curl -X POST "http://ORCHESTRATOR:8001/internal/runpod/warm?warm=true"
```

Metric `idp_runpod_warm` = 1 trên Grafana.

---

## 8. Model và checkpoint


| Model                                 | Vai trò            | Deploy                 |
| ------------------------------------- | ------------------ | ---------------------- |
| `mit_unet_doctamper` (MiT-B2 + U-Net) | Phát hiện gian lận | preprocess, file `.pt` |
| `InternVL3_5-8B-Flash`                | Trích xuất hóa đơn | RunPod + lmdeploy      |


**Prompt:** quản lý qua DB bảng `prompt_profiles` hoặc API `POST /v1/prompt-profiles`. Seed mặc định: [scripts/seed_prompts.sql](../scripts/seed_prompts.sql).

**Rules:** `POST /v1/rules` — JSON Logic (engine trong `idp-app`).

---

## 9. Monitoring và cảnh báo

### 9.1 Stack

Triển khai từ [infra/monitoring/](../infra/monitoring/):

- Prometheus — scrape `:8000–8003/metrics`
- Grafana — dashboard `idp-pipeline-overview.json`
- Alertmanager — route Slack/email
- Pushgateway — metrics từ RunPod trước khi pod stop

Hướng dẫn ngắn: [infra/monitoring/README.md](../infra/monitoring/README.md).

### 9.2 Alert quan trọng


| Alert                          | Ý nghĩa                    | Runbook                                                               |
| ------------------------------ | -------------------------- | --------------------------------------------------------------------- |
| `ExtractionThroughputDegraded` | tok/s < 85% baseline       | [alert-extraction-degraded.md](runbooks/alert-extraction-degraded.md) |
| `RunPodCircuitOpen`            | Không gọi được extraction  | [runpod-cold-start.md](runbooks/runpod-cold-start.md)                 |
| `HighSchemaValidationFailures` | JSON extraction lỗi schema | Kiểm tra prompt / schema                                              |


### 9.3 SLO gợi ý


| Metric              | Mục tiêu (RunPod warm)      |
| ------------------- | --------------------------- |
| E2E p95             | < 30s                       |
| Batch utilization   | > 0.5 (trung bình batch/48) |
| Fraud short-circuit | Hoạt động, tiết kiệm GPU    |


---

## 10. Kiểm tra sau deploy

### 10.1 Health

```bash
curl https://api.your-domain.com/health          # app
curl http://INTERNAL:8001/health                 # orchestrator (nội bộ)
curl http://INTERNAL:8002/health                 # preprocess
curl https://RUNPOD_HOST:8003/health             # extraction
```

### 10.2 Smoke test

```bash
export APP_URL=https://api.your-domain.com
# Dùng URL ảnh HTTPS thật (GCS signed URL), không dùng data: URL
./scripts/smoke_e2e.sh
```

Kỳ vọng trạng thái cuối: `COMPLETED` hoặc `FRAUD_DETECTED`.

### 10.3 API tạo job

```bash
curl -X POST https://api.your-domain.com/v1/jobs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: test-001" \
  -d '{
    "image_urls": ["https://storage.googleapis.com/BUCKET/path/receipt.jpg"],
    "invoice_type": "receipt",
    "tenant_id": "your-tenant",
    "callback_url": "https://your-app.com/webhooks/idp"
  }'
```

### 10.4 Load test (tùy chọn)

```bash
k6 run -e APP_URL=https://api.your-domain.com scripts/perf/k6_create_jobs.js
python scripts/perf/baseline_compare.py <measured_tps>
```

---

## 11. Vận hành hàng ngày


| Việc                                 | Tần suất                |
| ------------------------------------ | ----------------------- |
| Backup Cloud SQL                     | Hàng ngày (automated)   |
| Xem Grafana dashboard                | Liên tục / on-call      |
| Warm RunPod trước giờ cao điểm       | Theo lịch               |
| Cập nhật `prompt_profiles` / `rules` | Không cần redeploy app  |
| Golden-set regression                | Đêm (CI hoặc cron)      |
| Rotate `CALLBACK_HMAC_SECRET`        | Theo chính sách bảo mật |


**Cập nhật version service:**

```bash
docker pull $REGISTRY/idp-app:NEW_TAG
docker compose -f docker-compose.prod.yml up -d idp-app
# Rolling tương tự orchestrator, preprocess
```

RunPod: rebuild image / restart pod khi đổi model extraction.

---

## 12. Rollback và khắc phục sự cố

### Rollback application

```bash
docker compose -f docker-compose.prod.yml up -d idp-app:idp-app:PREVIOUS_TAG
```

### RunPod không phản hồi

1. Kiểm tra `idp_circuit_breaker_state` trên Grafana
2. Restart pod RunPod
3. `POST /internal/runpod/warm?warm=true`
4. Xem log lmdeploy OOM → giảm `BATCH_MAX_SIZE` tạm thời

### Redis backlog

```bash
redis-cli XLEN preprocess.tasks
redis-cli XPENDING preprocess.tasks preprocess-workers
```

Scale thêm consumer preprocess (instance thứ 2, `consumer_name` khác).

### DB connection đầy

Tăng connection pool / max connections Cloud SQL.

---

## Phụ lục A — So sánh Local vs Production


| Hạng mục         | Local (`docker-compose.yml`) | Production               |
| ---------------- | ---------------------------- | ------------------------ |
| Storage          | MinIO                        | GCS                      |
| DB               | Postgres container           | Cloud SQL                |
| Redis            | Container                    | Memorystore              |
| Extraction       | `MOCK_MODE=true`             | RunPod + InternVL        |
| Fraud            | Mock nếu không có `.pt`      | Checkpoint thật          |
| `EXTRACTION_URL` | `http://idp-extraction:8003` | URL RunPod public/tunnel |


## Phụ lục B — Checklist deploy lần đầu

- `terraform apply` thành công
- Migration + seed SQL
- Secrets trong Secret Manager
- Images push lên registry
- Compose prod trên VM, 3 service GCP healthy
- DocTamper checkpoint trên preprocess
- RunPod pod + lmdeploy healthy
- `EXTRACTION_URL` trỏ đúng RunPod
- `MOCK_MODE=false` trên extraction
- Grafana + alert nhận notification
- Smoke E2E `COMPLETED`
- Callback webhook test với HMAC

---

## Liên kết trong repo


| Tài liệu     | Path                                                                    |
| ------------ | ----------------------------------------------------------------------- |
| Kiến trúc    | [architecture.md](architecture.md)                                      |
| API          | [api/README.md](api/README.md)                                          |
| Terraform    | [deploy/terraform/gcp/](../deploy/terraform/gcp/)                       |
| RunPod       | [deploy/runpod/](../deploy/runpod/)                                     |
| Docker local | [deploy/docker/docker-compose.yml](../deploy/docker/docker-compose.yml) |


Nếu cần file `docker-compose.prod.yml` mẫu hoặc script deploy tự động, có thể yêu cầu thêm trong Agent mode.