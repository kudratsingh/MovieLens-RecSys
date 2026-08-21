export const recommendationThresholds = {
  "checks{endpoint:recommendations}": ["rate==1"],
  "http_req_duration{endpoint:recommendations}": ["p(99)<100"],
  "http_req_failed{endpoint:recommendations}": ["rate==0"],
  "http_reqs{endpoint:recommendations}": ["rate>50"],
};
