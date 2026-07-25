#!/usr/bin/env bash
set -euo pipefail

AWS_PROFILE_NAME="${AWS_PROFILE_NAME-promty-prod}"
DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-E3RJ7YU3NUZQSF}"
FUNCTION_NAME="promty-spa-rewrite"
UPDATE_DISTRIBUTION="${PROMTY_UPDATE_DISTRIBUTION:-true}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FUNCTION_CODE_PATH="${SCRIPT_DIR}/promty-cloudfront-spa-rewrite.js"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT

aws_args=()
if [[ -n "${AWS_PROFILE_NAME}" ]]; then
  aws_args+=(--profile "${AWS_PROFILE_NAME}")
fi

if aws cloudfront describe-function "${aws_args[@]}" --name "${FUNCTION_NAME}" >/dev/null 2>&1; then
  function_etag="$(aws cloudfront describe-function "${aws_args[@]}" --name "${FUNCTION_NAME}" --stage DEVELOPMENT --query ETag --output text)"
  aws cloudfront update-function "${aws_args[@]}" \
    --name "${FUNCTION_NAME}" \
    --if-match "${function_etag}" \
    --function-config 'Comment=Promty SPA route rewrite,Runtime=cloudfront-js-2.0' \
    --function-code "fileb://${FUNCTION_CODE_PATH}" >/dev/null
else
  aws cloudfront create-function "${aws_args[@]}" \
    --name "${FUNCTION_NAME}" \
    --function-config 'Comment=Promty SPA route rewrite,Runtime=cloudfront-js-2.0' \
    --function-code "fileb://${FUNCTION_CODE_PATH}" >/dev/null
fi

function_etag="$(aws cloudfront describe-function "${aws_args[@]}" --name "${FUNCTION_NAME}" --stage DEVELOPMENT --query ETag --output text)"
aws cloudfront publish-function "${aws_args[@]}" \
  --name "${FUNCTION_NAME}" \
  --if-match "${function_etag}" >/dev/null

function_arn="$(aws cloudfront describe-function "${aws_args[@]}" --name "${FUNCTION_NAME}" --stage LIVE --query FunctionSummary.FunctionMetadata.FunctionARN --output text)"
if [[ "${UPDATE_DISTRIBUTION}" != "true" ]]; then
  echo "cloudfront_route_rewrite=published"
  exit 0
fi

aws cloudfront get-distribution-config "${aws_args[@]}" \
  --id "${DISTRIBUTION_ID}" > "${TEMP_DIR}/distribution.json"
distribution_etag="$(jq -r '.ETag' "${TEMP_DIR}/distribution.json")"

jq --arg function_arn "${function_arn}" '
  .DistributionConfig
  | .DefaultCacheBehavior.FunctionAssociations = {
      Quantity: 1,
      Items: [{ EventType: "viewer-request", FunctionARN: $function_arn }]
    }
  | .CustomErrorResponses = { Quantity: 0 }
  | .HttpVersion = "http2and3"
' "${TEMP_DIR}/distribution.json" > "${TEMP_DIR}/distribution-config.json"

aws cloudfront update-distribution "${aws_args[@]}" \
  --id "${DISTRIBUTION_ID}" \
  --if-match "${distribution_etag}" \
  --distribution-config "file://${TEMP_DIR}/distribution-config.json" >/dev/null
aws cloudfront wait distribution-deployed "${aws_args[@]}" --id "${DISTRIBUTION_ID}"

echo "cloudfront_route_rewrite=deployed"
