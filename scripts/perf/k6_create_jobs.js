import http from "k6/http";
import { check, sleep } from "k6";

const APP_URL = __ENV.APP_URL || "http://localhost:8000";
const IMG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

export const options = {
  vus: 5,
  duration: "30s",
  thresholds: {
    http_req_duration: ["p(95)<30000"],
    checks: ["rate>0.8"],
  },
};

export default function () {
  const payload = JSON.stringify({
    image_urls: [`data:image/png;base64,${IMG_B64}`],
    invoice_type: "receipt",
    tenant_id: "load-test",
  });
  const res = http.post(`${APP_URL}/v1/jobs`, payload, {
    headers: { "Content-Type": "application/json" },
  });
  check(res, { "created": (r) => r.status === 201 });
  sleep(1);
}
