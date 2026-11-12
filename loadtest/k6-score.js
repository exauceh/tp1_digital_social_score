// Load test of the scoring API with k6 (course n°3: "tests de charge et de performance").
//
// Arrival-rate executors are used on purpose: they send a fixed number of requests per second
// whatever the latency is (open model, like real users). When the API saturates, the latency grows
// and k6 reports `dropped_iterations` instead of silently slowing down.
//
// Scenario chosen with the SCENARIO environment variable:
//   ramp       progressive ramp-up (5 -> 150 requests/s over 4 minutes)
//   sustained  constant load (SUSTAINED_RATE requests/s for 3 minutes)
//   spike      sudden spike to 250 requests/s (autoscaling stress test)
//   failover   constant load (FAILOVER_RATE requests/s for 4 minutes) during which a pod or a node
//              is removed by hand
//
// The texts are synthetic and mix short, medium and long comments, some of them with an e-mail
// address or an IP address, so that the anonymisation step is exercised too.

import http from 'k6/http';
import { check } from 'k6';
import execution from 'k6/execution';

const BASE_URL = __ENV.BASE_URL || 'http://dss-api.dss.svc.cluster.local';
const API_KEY = __ENV.API_KEY;
const SCENARIO = __ENV.SCENARIO || 'ramp';
const SUSTAINED_RATE = Number(__ENV.SUSTAINED_RATE || 60);
const FAILOVER_RATE = Number(__ENV.FAILOVER_RATE || 40);
// Durations can be shortened for a quick check of the script (default: the real test).
const SUSTAINED_DURATION = __ENV.SUSTAINED_DURATION || '3m';
const FAILOVER_DURATION = __ENV.FAILOVER_DURATION || '4m';

const SHORT = [
  'Thank you for fixing the article, great work!',
  'You are a stupid idiot and nobody cares about your edits.',
  'I will find you and hurt you, John Smith, you will regret this.',
  'Those people are all vermin, go back to your country.',
  'Contact me at jane.doe@example.com or 192.168.0.12, this is nonsense',
];
const PARAGRAPH =
  'I do not agree with this edit. The sources you cite are outdated and the paragraph is ' +
  'poorly written. Please discuss on the talk page before reverting again, otherwise I will ' +
  'report you to the administrators. ';
const MEDIUM = [PARAGRAPH + 'Thanks for your understanding.', PARAGRAPH + SHORT[1]];
const LONG = [PARAGRAPH.repeat(4) + SHORT[4], PARAGRAPH.repeat(5) + SHORT[2]];

function pickText() {
  const dice = Math.random();
  if (dice < 0.5) return SHORT[Math.floor(Math.random() * SHORT.length)];
  if (dice < 0.8) return MEDIUM[Math.floor(Math.random() * MEDIUM.length)];
  return LONG[Math.floor(Math.random() * LONG.length)];
}

const SCENARIOS = {
  ramp: {
    executor: 'ramping-arrival-rate',
    startRate: 5,
    timeUnit: '1s',
    preAllocatedVUs: 50,
    maxVUs: 600,
    stages: [
      { target: 25, duration: '60s' },
      { target: 50, duration: '60s' },
      { target: 100, duration: '60s' },
      { target: 150, duration: '60s' },
    ],
  },
  sustained: {
    executor: 'constant-arrival-rate',
    rate: SUSTAINED_RATE,
    timeUnit: '1s',
    duration: SUSTAINED_DURATION,
    preAllocatedVUs: 50,
    maxVUs: 400,
  },
  spike: {
    executor: 'ramping-arrival-rate',
    startRate: 10,
    timeUnit: '1s',
    preAllocatedVUs: 100,
    maxVUs: 800,
    stages: [
      { target: 10, duration: '30s' },
      { target: 250, duration: '10s' },
      { target: 250, duration: '2m' },
      { target: 10, duration: '10s' },
      { target: 10, duration: '60s' },
    ],
  },
  failover: {
    executor: 'constant-arrival-rate',
    rate: FAILOVER_RATE,
    timeUnit: '1s',
    duration: FAILOVER_DURATION,
    preAllocatedVUs: 50,
    maxVUs: 300,
  },
};

// Ramp only: every request is tagged with the rate that was requested when it was sent, by slices
// of 15 s, so that the summary gives the percentiles per load level and shows where the latency
// starts to explode. The rate is interpolated at the middle of each slice.
const RAMP_SLICE_SECONDS = 15;
const RAMP_STAGE_SECONDS = 60;
const RAMP_RATES = [5, 25, 50, 100, 150]; // startRate, then the target of each stage

function rampRateAt(elapsedSeconds) {
  const stage = Math.min(Math.floor(elapsedSeconds / RAMP_STAGE_SECONDS), RAMP_RATES.length - 2);
  const fraction = (elapsedSeconds - stage * RAMP_STAGE_SECONDS) / RAMP_STAGE_SECONDS;
  return RAMP_RATES[stage] + fraction * (RAMP_RATES[stage + 1] - RAMP_RATES[stage]);
}

function rampSliceTag(elapsedSeconds) {
  const slice = Math.min(
    Math.floor(elapsedSeconds / RAMP_SLICE_SECONDS),
    ((RAMP_RATES.length - 1) * RAMP_STAGE_SECONDS) / RAMP_SLICE_SECONDS - 1,
  );
  const middle = (slice + 0.5) * RAMP_SLICE_SECONDS;
  return `rate_${String(Math.round(rampRateAt(middle))).padStart(3, '0')}`;
}

const thresholds = {};
if (SCENARIO === 'ramp') {
  // A threshold that is always true: k6 only prints the sub-metrics that have one.
  const slices = ((RAMP_RATES.length - 1) * RAMP_STAGE_SECONDS) / RAMP_SLICE_SECONDS;
  for (let slice = 0; slice < slices; slice += 1) {
    const tag = rampSliceTag(slice * RAMP_SLICE_SECONDS);
    thresholds[`http_req_duration{slice:${tag}}`] = ['max>=0'];
  }
}

export const options = {
  scenarios: { [SCENARIO]: SCENARIOS[SCENARIO] },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  thresholds,
};

let logged = 0;

export default function () {
  const tags = {};
  if (SCENARIO === 'ramp') {
    tags.slice = rampSliceTag((Date.now() - new Date(execution.scenario.startTime).getTime()) / 1000);
  }
  const response = http.post(`${BASE_URL}/score`, JSON.stringify({ text: pickText() }), {
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    timeout: '10s',
    tags,
  });
  const ok = check(response, { 'status is 200': (r) => r.status === 200 });
  // The first failures are logged with their time (never the text), to line them up with the
  // moment a pod or a node was removed.
  if (!ok && logged < 40) {
    logged += 1;
    console.warn(`FAILED at ${new Date().toISOString()} status=${response.status} error=${response.error_code}`);
  }
}
