// k6 load test for the control plane.
//
// This measures the API's submission path, not the crawlers: the point is to
// find where accepting jobs starts to hurt, and to prove that backpressure
// refuses work instead of letting the queue grow without bound.
//
//   k6 run -e BASE_URL=http://localhost:8000 -e API_KEY=dev-key-write loadtest/jobs.js
//
// The default target is deliberately the HTTP one. Pointing a load test at a
// browser target would hammer a real site with a real browser, which is not
// something a load test should do by accident.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'dev-key-write';
const TARGET = __ENV.TARGET || 'federal_register';

const submitDuration = new Trend('submit_duration', true);
const backpressure = new Counter('backpressure_429');
const rateLimited = new Counter('rate_limited_429');
const accepted = new Rate('accepted');

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: 5,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
    burst: {
      executor: 'ramping-arrival-rate',
      startTime: '35s',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 20,
      maxVUs: 200,
      stages: [
        { target: 50, duration: '30s' },
        { target: 200, duration: '30s' },
        { target: 0, duration: '10s' },
      ],
    },
  },
  thresholds: {
    // The API must stay responsive even while refusing work. A 429 is a
    // successful outcome here; a timeout or a 500 is not.
    'http_req_failed{expected_response:true}': ['rate<0.01'],
    submit_duration: ['p(95)<500'],
    'http_req_duration{scenario:steady}': ['p(99)<1000'],
  },
};

function headers() {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY,
    'Idempotency-Key': `k6-${__VU}-${__ITER}`,
  };
}

export default function () {
  const payload = JSON.stringify({ target: TARGET, max_pages: 2, output_format: 'json' });
  const res = http.post(`${BASE_URL}/jobs`, payload, {
    headers: headers(),
    tags: { name: 'POST /jobs' },
  });

  submitDuration.add(res.timings.duration);
  accepted.add(res.status === 202);

  if (res.status === 429) {
    const reason = res.body && res.body.includes('capacity') ? 'queue' : 'api';
    if (reason === 'queue') backpressure.add(1);
    else rateLimited.add(1);
  }

  check(res, {
    'accepted or deliberately refused': (r) => r.status === 202 || r.status === 429,
    'never a server error': (r) => r.status < 500,
    'refusals carry Retry-After': (r) => r.status !== 429 || !!r.headers['Retry-After'],
  });

  sleep(0.1);
}

export function handleSummary(data) {
  const acceptedRate = data.metrics.accepted ? data.metrics.accepted.values.rate : 0;
  return {
    stdout:
      `\nsubmitted: ${data.metrics.iterations.values.count}` +
      `\naccepted:  ${(acceptedRate * 100).toFixed(1)}%` +
      `\np95 submit: ${data.metrics.submit_duration.values['p(95)'].toFixed(1)} ms\n`,
  };
}
